-- CharSnapshot core: storage, events, scan scheduling, slash command.
--
-- SavedVariables layout (CharSnapshotDB, account-wide so one file holds every character):
--   schema, accountMacros, chars[realm][name] = snapshot
-- A snapshot holds one table per section (player, equipped, bags, bank, mail, spells, ...). Sections that can
-- only be read with an NPC window open (bank, mail, taxi, stable, professions) keep their last capture and
-- carry their own scannedAt time. tools/char_snapshot.py reads the file and turns it into JSON / SQL.

local ADDON, ns = ...
local CB = {}
ns.CB = CB
CB.SCHEMA = 1
CB.VERSION = (C_AddOns and C_AddOns.GetAddOnMetadata or GetAddOnMetadata)(ADDON, "Version")

local MAX_HISTORY = 300
local MAX_ERRORS = 30
local STALE_DAYS = 14

local frame = CreateFrame("Frame")
local handlers = {}

function CB.Print(msg)
  DEFAULT_CHAT_FRAME:AddMessage("|cff66bbffCharSnapshot|r: " .. msg)
end

-- Registering an event the client doesn't have raises an error, which would abort loading this file, so
-- events are checked first and unknown ones are logged instead.
function CB.On(event, fn)
  if not handlers[event] then
    if C_EventUtils and C_EventUtils.IsEventValid and not C_EventUtils.IsEventValid(event) then
      CB.badEvents = CB.badEvents or {}
      table.insert(CB.badEvents, event)
      return
    end
    local ok = pcall(frame.RegisterEvent, frame, event)
    if not ok then
      CB.badEvents = CB.badEvents or {}
      table.insert(CB.badEvents, event)
      return
    end
    handlers[event] = {}
  end
  table.insert(handlers[event], fn)
end

frame:SetScript("OnEvent", function(_, event, ...)
  for _, fn in ipairs(handlers[event]) do
    local ok, err = pcall(fn, event, ...)
    if not ok then CB.LogError(event, err) end
  end
end)

function CB.LogError(where, err)
  local char = CB.char
  if not char then return end
  char.errors = char.errors or {}
  table.insert(char.errors, { t = time(), where = where, err = tostring(err) })
  while #char.errors > MAX_ERRORS do table.remove(char.errors, 1) end
end

-- Run a collector, storing its result in the snapshot even if other collectors fail.
function CB.Collect(section, fn, ...)
  local ok, result = pcall(fn, ...)
  if ok then
    if result ~= nil then CB.char[section] = result end
  else
    CB.LogError(section, result)
  end
end

-- Coalesce bursts of events (BAG_UPDATE fires per slot) into one call. An event that arrives while a call is
-- pending runs it once more afterwards, so data that arrives a moment later (a recipe list) is not missed.
local pending = {}
function CB.Debounce(key, delay, fn)
  if pending[key] then
    pending[key] = "again"
    return
  end
  pending[key] = true
  C_Timer.After(delay, function()
    local again = pending[key] == "again"
    pending[key] = nil
    local ok, err = pcall(fn)
    if not ok then CB.LogError(key, err) end
    if again then CB.Debounce(key, delay, fn) end
  end)
end

local function InitDB()
  CharSnapshotDB = CharSnapshotDB or {}
  local db = CharSnapshotDB
  db.schema = CB.SCHEMA
  db.chars = db.chars or {}
  local realm, name = GetRealmName(), UnitName("player")
  db.chars[realm] = db.chars[realm] or {}
  db.chars[realm][name] = db.chars[realm][name] or { firstSeen = time() }
  CB.db, CB.char = db, db.chars[realm][name]
end

local function Meta()
  local version, build, date, interface = GetBuildInfo()
  return {
    addonVersion = CB.VERSION,
    schema = CB.SCHEMA,
    gameVersion = version,
    build = tonumber(build),
    buildDate = date,
    interface = interface,
    locale = GetLocale(),
    region = GetCurrentRegion and GetCurrentRegion() or nil,
    realm = GetRealmName(),
    name = UnitName("player"),
    guid = UnitGUID("player"),
    savedAt = time(),
    serverTime = GetServerTime(),
  }
end

-- Sections readable without an NPC window, in scan order (cooldowns read the scanned bags and spells).
local SECTIONS = {
  { "player", function() return ns.Character.Player() end },
  { "position", function() return ns.Character.Position() end },
  { "equipped", function() return ns.Items.Equipped() end },
  { "bags", function() return ns.Items.Bags() end },
  { "spells", function() return ns.Character.Spells() end },
  { "talents", function() return ns.Character.Talents() end },
  { "skills", function() return ns.Character.Skills() end },
  { "reputation", function() return ns.Character.Reputation() end },
  { "quests", function() return ns.Character.Quests() end },
  { "actions", function() return ns.Character.Actions() end },
  { "macros", function() return ns.Character.Macros() end },
  { "auras", function() return ns.Character.Auras() end },
  { "cooldowns", function() return ns.Character.Cooldowns() end },
  { "social", function() return ns.Character.Social() end },
  { "pet", function() return ns.Character.Pet() end },
  { "explored", function() return ns.Character.Explored() end },
}

-- The client's data is only trustworthy in the world: during loading screens and logout it reports empty
-- bags, no gear and no money. The backpack always has slots, so it doubles as the check.
local inWorld = false
function CB.Ready()
  return inWorld and (C_Container.GetContainerNumSlots(0) or 0) > 0
end

local function Summary(reason)
  local char, p, items = CB.char, CB.char.player or {}, 0
  for _, bag in pairs(char.bags or {}) do
    for _ in pairs(bag.items or {}) do items = items + 1 end
  end
  for _ in pairs(char.equipped or {}) do items = items + 1 end
  return { t = time(), st = GetServerTime(), reason = reason, level = p.level, xp = p.xp, money = p.money,
    played = p.played and p.played.total, items = items }
end

-- Scan the given sections (a set of names), or all of them. Returns false when the client isn't ready.
function CB.Scan(reason, only)
  if not CB.Ready() then return false end
  local char = CB.char
  for _, s in ipairs(SECTIONS) do
    if not only or only[s[1]] then CB.Collect(s[1], s[2]) end
  end
  if not only or only.macros then
    local ok, err = pcall(ns.Character.AccountMacros, CB.db)
    if not ok then CB.LogError("accountMacros", err) end
  end
  char.meta = Meta()
  char.meta.reason = reason
  if not only then char.lastFullScan = char.meta.savedAt end
  -- Append-only history (at most one entry per 10 minutes, plus every full scan), so a verifier can check
  -- that level, money, played time and item count develop consistently.
  char.history = char.history or {}
  local last = char.history[#char.history]
  if not only or not last or time() - last.t >= 600 then
    table.insert(char.history, Summary(reason))
    while #char.history > MAX_HISTORY do table.remove(char.history, 1) end
  end
  return true
end

function CB.FullScan(reason)
  return CB.Scan(reason)
end

-- Events mark sections as changed; a ticker rescans changed sections every 15 s, outside combat.
local DIRTY_EVENTS = {
  BAG_UPDATE_DELAYED = { "bags", "cooldowns" },
  PLAYER_EQUIPMENT_CHANGED = { "equipped", "player" },
  PLAYER_MONEY = { "player" },
  PLAYER_XP_UPDATE = { "player" },
  PLAYER_LEVEL_UP = { "player", "spells", "talents", "skills" },
  PLAYER_GUILD_UPDATE = { "player" },
  PLAYER_PVP_KILLS_CHANGED = { "player" },
  SPELLS_CHANGED = { "spells", "actions" },
  SKILL_LINES_CHANGED = { "skills", "spells" },
  CHARACTER_POINTS_CHANGED = { "talents", "spells", "player" },
  UPDATE_FACTION = { "reputation" },
  QUEST_TURNED_IN = { "quests", "player" },
  QUEST_ACCEPTED = { "quests" },
  QUEST_LOG_UPDATE = { "quests" },
  ACTIONBAR_SLOT_CHANGED = { "actions" },
  UPDATE_MACROS = { "macros" },
  UNIT_PET = { "pet" },
  ZONE_CHANGED_NEW_AREA = { "position" },
  ZONE_CHANGED = { "position" },
  MAP_EXPLORATION_UPDATED = { "explored" },
  FRIENDLIST_UPDATE = { "social" },
  UNIT_AURA = { "auras" },
}
local dirty = {}
for event, sections in pairs(DIRTY_EVENTS) do
  CB.On(event, function(_, unit)
    if event == "UNIT_AURA" and unit ~= "player" then return end
    if event == "UNIT_PET" and unit ~= "player" then return end
    for _, s in ipairs(sections) do dirty[s] = true end
  end)
end

local function Tick()
  if not next(dirty) or UnitAffectingCombat("player") then return end
  local only = dirty
  dirty = {}
  if not CB.Scan("update", only) then
    for s in pairs(only) do dirty[s] = true end -- not in the world yet; retry next tick
  end
end

-- /played without the chat message. Since 1.15.8 chat calls ChatFrameUtil.DisplayTimePlayed, and every
-- chat frame receives the event, so swallow the message for one frame.
local suppressPlayed = false
local function RequestPlayed()
  if ChatFrameUtil and ChatFrameUtil.DisplayTimePlayed and not CB.playedHooked then
    local original = ChatFrameUtil.DisplayTimePlayed
    ChatFrameUtil.DisplayTimePlayed = function(...)
      if suppressPlayed then return end
      return original(...)
    end
    CB.playedHooked = true
  end
  suppressPlayed = CB.playedHooked
  RequestTimePlayed()
end

CB.On("TIME_PLAYED_MSG", function(_, total, level)
  CB.played = { total = total, level = level, at = time() }
  C_Timer.After(0, function() suppressPlayed = false end)
end)

CB.On("ADDON_LOADED", function(_, name)
  if name ~= ADDON then return end
  InitDB()
  for _, event in ipairs(CB.badEvents or {}) do CB.LogError("RegisterEvent", "unknown event " .. event) end
end)

CB.On("PLAYER_ENTERING_WORLD", function() inWorld = true end)
CB.On("PLAYER_LEAVING_WORLD", function() inWorld = false end)

CB.On("PLAYER_LOGIN", function()
  -- Item, quest and reputation data arrive over the first seconds; scan once things have settled.
  local function loginScan(tries)
    if CB.FullScan("login") then
      CB.Reminder()
    elseif tries > 0 then
      C_Timer.After(5, function() loginScan(tries - 1) end)
    end
  end
  C_Timer.After(10, function() loginScan(12) end)
  C_Timer.NewTicker(15, function()
    local ok, err = pcall(Tick)
    if not ok then CB.LogError("tick", err) end
  end)
  local ok, err = pcall(RequestPlayed)
  if not ok then CB.LogError("RequestTimePlayed", err) end
  if C_FriendList and C_FriendList.ShowFriends then C_FriendList.ShowFriends() end
end)

-- No scan at logout: by then the client has emptied bags, gear, money and skills. Only played time, which
-- is computed from timestamps, is brought up to date.
CB.On("PLAYER_LOGOUT", function()
  local p = CB.char and CB.char.player
  if p and CB.played then
    p.played = { total = CB.played.total + (time() - CB.played.at), level = CB.played.level + (time() - CB.played.at) }
  end
end)

-- What still needs an NPC visit, so the snapshot is complete.
function CB.Missing()
  local char, now, missing = CB.char, time(), {}
  local function check(label, t)
    if not t then
      table.insert(missing, label .. " (never)")
    elseif now - t > STALE_DAYS * 86400 then
      table.insert(missing, string.format("%s (%d days old)", label, (now - t) / 86400))
    end
  end
  check("bank", char.bank and char.bank.scannedAt)
  check("mailbox", char.mail and char.mail.scannedAt)
  local taxi = char.taxi and char.taxi.continents or {}
  local faction = UnitFactionGroup("player")
  check("flight master in Eastern Kingdoms", taxi[1415])
  check("flight master in Kalimdor", taxi[1414])
  if select(2, UnitClass("player")) == "HUNTER" then
    check("stable master", char.stable and char.stable.scannedAt)
  end
  -- Profession windows aren't required: known recipes are found with IsSpellKnown (checked in game).
  return missing, faction
end

function CB.Reminder()
  local missing = CB.Missing()
  if #missing > 0 then
    CB.Print("to complete this snapshot, open: " .. table.concat(missing, ", ") .. ". /charsnapshot for status.")
  end
end

local function Age(t)
  if not t then return "|cffff5555never|r" end
  local d = time() - t
  if d < 3600 then return string.format("%d min ago", d / 60) end
  if d < 86400 then return string.format("%.1f h ago", d / 3600) end
  return string.format("%.1f days ago", d / 86400)
end

SLASH_CHARSNAPSHOT1 = "/charsnapshot"
SLASH_CHARSNAPSHOT2 = "/csnap"
SlashCmdList.CHARSNAPSHOT = function(msg)
  msg = strtrim(msg or ""):lower()
  local char = CB.char
  if msg == "scan" then
    if CB.FullScan("manual") then
      CB.Print("scanned. Saved to disk on logout or /reload.")
    else
      CB.Print("not ready (loading screen?); try again in a moment.")
    end
  elseif msg == "errors" then
    for _, e in ipairs(char.errors or {}) do
      CB.Print(date("%Y-%m-%d %H:%M", e.t) .. " " .. e.where .. ": " .. e.err)
    end
    if not char.errors or #char.errors == 0 then CB.Print("no errors.") end
  else
    CB.Print("full scan " .. Age(char.lastFullScan) .. ", bank " .. Age(char.bank and char.bank.scannedAt)
      .. ", mail " .. Age(char.mail and char.mail.scannedAt) .. ".")
    local missing = CB.Missing()
    CB.Print(#missing == 0 and "nothing missing." or ("still to open: " .. table.concat(missing, ", ")))
    CB.Print("/charsnapshot scan = scan now, /charsnapshot errors = show collector errors.")
  end
end
