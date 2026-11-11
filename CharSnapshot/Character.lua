-- Character state readable at any time: identity, money, honor, position, spells, talents, skills,
-- reputation, quests, action bars, macros, auras, cooldowns, friends, active pet, explored areas.

local _, ns = ...
local CB = ns.CB
local C = {}
ns.Character = C

local SELF_FOUND_AURA = 431567 -- "Self-Found Adventurer"
-- Spells whose names are the localized profession names (their recipe windows), see ProfessionNames().
local PROFESSION_SPELLS = { 2259, 2018, 7411, 4036, 2108, 3908, 2550, 3273, 2575, 2842 }

local function Sorted(set)
  local list = {}
  for k in pairs(set) do list[#list + 1] = k end
  table.sort(list)
  return list
end

local function IsKnown(spellID)
  if C_SpellBook and C_SpellBook.IsSpellKnown then return C_SpellBook.IsSpellKnown(spellID) end
  return IsPlayerSpell and IsPlayerSpell(spellID)
end

function C.Player()
  local _, raceFile, raceID = UnitRace("player")
  local _, classFile, classID = UnitClass("player")
  local hk, dk, highestRank = GetPVPLifetimeStats()
  local lwHK, lwDK, lwCP, lwRank = GetPVPLastWeekStats()
  local twHK, twCP = GetPVPThisWeekStats()
  local guild, guildRank, guildRankIndex = GetGuildInfo("player")
  local played = CB.played and {
    total = CB.played.total + (time() - CB.played.at),
    level = CB.played.level + (time() - CB.played.at),
  }
  local selfFound
  if C_PlayerInfo and C_PlayerInfo.IsSelfFoundActive then
    selfFound = C_PlayerInfo.IsSelfFoundActive()
  end
  if not selfFound and C_UnitAuras and C_UnitAuras.GetPlayerAuraBySpellID then
    selfFound = C_UnitAuras.GetPlayerAuraBySpellID(SELF_FOUND_AURA) ~= nil
  end
  return {
    name = UnitName("player"), realm = GetRealmName(), guid = UnitGUID("player"),
    race = raceFile, raceID = raceID, class = classFile, classID = classID,
    sex = UnitSex("player"), -- 2 male, 3 female
    faction = UnitFactionGroup("player"),
    level = UnitLevel("player"), xp = UnitXP("player"), xpMax = UnitXPMax("player"),
    rested = GetXPExhaustion(), restState = GetRestState(), resting = IsResting() or nil,
    money = GetMoney(),
    played = played,
    health = UnitHealth("player"), healthMax = UnitHealthMax("player"),
    power = UnitPower("player"), powerType = UnitPowerType("player"),
    unspentTalentPoints = UnitCharacterPoints("player"),
    guild = guild and { name = guild, rank = guildRank, rankIndex = guildRankIndex } or nil,
    honor = {
      hk = hk, dk = dk, highestRank = highestRank, rank = UnitPVPRank("player"), progress = GetPVPRankProgress(),
      lastWeek = { hk = lwHK, dk = lwDK, cp = lwCP, rank = lwRank },
      thisWeek = { hk = twHK, cp = twCP },
    },
    hardcore = C_GameRules and C_GameRules.IsHardcoreActive and C_GameRules.IsHardcoreActive() or nil,
    season = C_Seasons and C_Seasons.GetActiveSeason and C_Seasons.GetActiveSeason() or nil,
    selfFound = selfFound or nil,
    dead = UnitIsDeadOrGhost("player") or nil,
    bind = GetBindLocation(),
  }
end

-- World position, in server coordinates: UnitPosition's first value is the server's position_x (north),
-- the second its position_y (west). Addon libraries such as HereBeDragons call them "y, x" because they
-- use map orientation. It can be nil inside instances, in which case the last outdoor position is kept.
function C.Position()
  local x, y, z, map = UnitPosition("player")
  local where = {
    zone = GetRealZoneText(), subzone = GetSubZoneText(), uiMap = C_Map.GetBestMapForUnit("player"),
    inInstance = IsInInstance() or nil, at = time(),
  }
  if not x then
    local last = CB.char.position or {}
    last.current = where
    return last
  end
  where.x, where.y, where.z, where.map, where.facing = x, y, z, map, GetPlayerFacing()
  return where
end

-- Known spells: the spellbook (highest ranks of class spells), plus every candidate spell from the client's
-- skill-line and talent data (Data.lua) that the character knows: recipes, lower ranks, proficiencies,
-- passives and talent ranks that never appear in the spellbook.
function C.Spells()
  local book, known = {}, {}
  for tab = 1, GetNumSpellTabs() do
    local _, _, offset, numSlots = GetSpellTabInfo(tab)
    for i = offset + 1, offset + numSlots do
      local slotType, id = GetSpellBookItemInfo(i, BOOKTYPE_SPELL)
      if slotType == "SPELL" and id then book[id] = true; known[id] = true end
    end
  end
  for _, id in ipairs(ns.Data.spells) do
    if IsKnown(id) then known[id] = true end
  end
  return { book = Sorted(book), known = Sorted(known) }
end

-- Talents with their Talent.db2 IDs (talentID), so the importer can map rank -> rank spell.
function C.Talents()
  local out = { unspent = UnitCharacterPoints("player"), tabs = {}, talents = {} }
  for tab = 1, GetNumTalentTabs() do
    local _, name, _, _, _, _, pointsSpent = C_SpecializationInfo.GetSpecializationInfo(tab)
    out.tabs[tab] = { name = name, points = pointsSpent }
    for i = 1, GetNumTalents(tab) do
      local t = C_SpecializationInfo.GetTalentInfo({ specializationIndex = tab, talentIndex = i })
      if t and t.rank and t.rank > 0 then
        table.insert(out.talents, {
          id = t.talentID, rank = t.rank, maxRank = t.maxRank, name = t.name, tab = tab, tier = t.tier,
          column = t.column,
        })
      end
    end
  end
  return out
end

-- Walk a header list with everything expanded, then collapse what the user had collapsed.
-- getInfo(i) returns name, isHeader, isCollapsed.
local function WithExpanded(count, getInfo, expand, collapse, fn)
  local collapsed = {}
  for i = count(), 1, -1 do
    local name, isHeader, isCollapsed = getInfo(i)
    if isHeader and isCollapsed then
      collapsed[name] = true
      expand(i)
    end
  end
  local ok, result = pcall(fn)
  for i = count(), 1, -1 do
    local name, isHeader, isCollapsed = getInfo(i)
    if isHeader and not isCollapsed and collapsed[name] then collapse(i) end
  end
  if not ok then error(result, 0) end
  return result
end

-- Skills (localized names; the importer maps them to SkillLine IDs).
function C.Skills()
  return WithExpanded(GetNumSkillLines,
    function(i)
      local name, isHeader, isExpanded = GetSkillLineInfo(i)
      return name, isHeader, not isExpanded
    end,
    ExpandSkillHeader, CollapseSkillHeader,
    function()
      local out, header = {}, nil
      for i = 1, GetNumSkillLines() do
        local name, isHeader, _, rank, temp, modifier, max, abandonable = GetSkillLineInfo(i)
        if isHeader then
          header = name
        elseif name then
          table.insert(out, {
            name = name, rank = rank, max = max, mod = modifier ~= 0 and modifier or nil,
            temp = temp ~= 0 and temp or nil, header = header, abandonable = abandonable or nil,
          })
        end
      end
      if #out == 0 then return nil end -- skill data not loaded; keep the last capture
      return { locale = GetLocale(), list = out }
    end)
end

-- Reputation. value is the absolute reputation (the importer subtracts the race/class base).
function C.Reputation()
  return WithExpanded(GetNumFactions,
    function(i)
      local name, _, _, _, _, _, _, _, isHeader, isCollapsed = GetFactionInfo(i)
      return name, isHeader, isCollapsed
    end,
    ExpandFactionHeader, CollapseFactionHeader,
    function()
      local out, header = {}, nil
      for i = 1, GetNumFactions() do
        local name, _, standing, _, _, value, atWar, canToggle, isHeader, _, hasRep, watched, _, factionID =
          GetFactionInfo(i)
        if isHeader then header = name end
        if not isHeader or hasRep then
          table.insert(out, {
            id = factionID, name = name, standing = standing, value = value, atWar = atWar or nil,
            canToggleAtWar = canToggle or nil, watched = watched or nil, header = header,
            inactive = IsFactionInactive and IsFactionInactive(i) or nil,
          })
        end
      end
      return out
    end)
end

local function Objectives(questID, index)
  local list = {}
  local objectives = C_QuestLog.GetQuestObjectives and C_QuestLog.GetQuestObjectives(questID)
  if objectives and #objectives > 0 then
    for i, o in ipairs(objectives) do
      list[i] = { text = o.text, type = o.type, done = o.finished or nil, have = o.numFulfilled, need = o.numRequired }
    end
    return list
  end
  for i = 1, GetNumQuestLeaderBoards(index) do
    local text, kind, done = GetQuestLogLeaderBoard(i, index)
    local have, need = (text or ""):match("(%d+)/(%d+)")
    list[i] = { text = text, type = kind, done = done or nil, have = tonumber(have), need = tonumber(need) }
  end
  return list
end

-- Completed quests (server flags) plus the quest log with objective progress and timers.
function C.Quests()
  local completed = {}
  for id in pairs(GetQuestsCompleted() or {}) do completed[id] = true end
  local timers = {}
  local times = { GetQuestTimers() }
  for i = 1, #times do
    local index = GetQuestIndexForTimer(i)
    if index then timers[index] = times[i] end
  end
  local log = WithExpanded(GetNumQuestLogEntries,
    function(i)
      local title, _, _, isHeader, isCollapsed = GetQuestLogTitle(i)
      return title, isHeader, isCollapsed
    end,
    ExpandQuestHeader, CollapseQuestHeader,
    function()
      local out = {}
      for i = 1, GetNumQuestLogEntries() do
        local title, level, _, isHeader, _, isComplete, frequency, questID = GetQuestLogTitle(i)
        if not isHeader and questID then
          table.insert(out, {
            id = questID, title = title, level = level, complete = isComplete, frequency = frequency,
            timeLeft = timers[i], objectives = Objectives(questID, i),
          })
        end
      end
      return out
    end)
  return { completed = Sorted(completed), log = log }
end

-- Action buttons 1..120 (slot n is button n-1 on the server). Macros are identified by name.
function C.Actions()
  local out = {}
  for slot = 1, 144 do
    local kind, id, subType = GetActionInfo(slot)
    if kind then
      out[slot] = { t = kind, id = id, sub = subType, macro = kind == "macro" and GetActionText(slot) or nil }
    end
  end
  return out
end

function C.Macros()
  local _, numChar = GetNumMacros()
  local out = {}
  for i = MAX_ACCOUNT_MACROS + 1, MAX_ACCOUNT_MACROS + numChar do
    local name, icon, body = GetMacroInfo(i)
    if name then table.insert(out, { name = name, icon = icon, body = body }) end
  end
  return out
end

function C.AccountMacros(db)
  local numAccount = GetNumMacros()
  local out = { savedAt = time(), list = {} }
  for i = 1, numAccount do
    local name, icon, body = GetMacroInfo(i)
    if name then table.insert(out.list, { name = name, icon = icon, body = body }) end
  end
  db.accountMacros = out
end

-- Buffs and debuffs with remaining seconds (GetTime() values mean nothing after a relog).
function C.Auras()
  local out, now = {}, GetTime()
  for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
    for i = 1, 40 do
      local a = C_UnitAuras.GetAuraDataByIndex("player", i, filter)
      if not a then break end
      table.insert(out, {
        id = a.spellId, name = a.name, count = a.applications ~= 0 and a.applications or nil,
        remaining = (a.expirationTime and a.expirationTime > 0) and math.floor(a.expirationTime - now) or nil,
        harmful = filter == "HARMFUL" or nil,
      })
    end
  end
  return out
end

-- Spell and item cooldowns longer than a global cooldown, as remaining seconds.
function C.Cooldowns()
  local out, now = { spells = {}, items = {} }, GetTime()
  for _, id in ipairs(CB.char.spells and CB.char.spells.book or {}) do
    local start, duration = GetSpellCooldown(id)
    if start and duration and duration > 2 and start > 0 then
      out.spells[id] = math.floor(start + duration - now)
    end
  end
  local seen = {}
  local function item(itemID)
    if not itemID or seen[itemID] then return end
    seen[itemID] = true
    local start, duration = C_Container.GetItemCooldown(itemID)
    if start and duration and duration > 2 and start > 0 then
      out.items[itemID] = math.floor(start + duration - now)
    end
  end
  for _, bag in pairs(CB.char.bags or {}) do
    for _, it in pairs(bag.items or {}) do item(tonumber(it.s:match("item:(%d+)"))) end
  end
  for slot = 1, 19 do item(GetInventoryItemID("player", slot)) end
  return out
end

function C.Social()
  local out = { friends = {}, ignores = {} }
  for i = 1, C_FriendList.GetNumFriends() or 0 do
    local info = C_FriendList.GetFriendInfoByIndex(i)
    if info and info.name then table.insert(out.friends, { name = info.name, notes = info.notes }) end
  end
  for i = 1, C_FriendList.GetNumIgnores() or 0 do
    table.insert(out.ignores, C_FriendList.GetIgnoreName(i))
  end
  return out
end

-- The active pet. Returns nil when no pet is out, so the last capture is kept.
function C.Pet()
  if not UnitExists("pet") then return nil end
  local _, isHunterPet = HasPetUI()
  local guid = UnitGUID("pet")
  local loyaltyText = GetPetLoyalty and GetPetLoyalty()
  local tpTotal, tpSpent = GetPetTrainingPoints()
  local xp, xpNext = GetPetExperience()
  local happiness = GetPetHappiness()
  local out = {
    seenAt = time(), name = UnitName("pet"), guid = guid,
    creatureID = guid and tonumber((select(6, strsplit("-", guid)))), -- Pet-0-server-instance-zone-npcID-spawn
    hunter = isHunterPet or nil, level = UnitLevel("pet"), family = UnitCreatureFamily("pet"),
    xp = xp, xpNext = xpNext, happiness = happiness, loyaltyText = loyaltyText,
    loyalty = loyaltyText and tonumber(loyaltyText:match("(%d+)")) or nil,
    trainingPoints = tpTotal and { total = tpTotal, spent = tpSpent } or nil,
    health = UnitHealth("pet"), healthMax = UnitHealthMax("pet"), spells = {}, actions = {},
  }
  for i = 1, (HasPetSpells() or 0) do
    local slotType, id = GetSpellBookItemInfo(i, BOOKTYPE_PET)
    if id then
      local autoAllowed, autoOn = GetSpellAutocast(i, BOOKTYPE_PET)
      table.insert(out.spells, { id = id, type = slotType, autocast = autoAllowed and (autoOn and true or false) or nil })
    end
  end
  for i = 1, NUM_PET_ACTION_SLOTS or 10 do
    local name, _, isToken, isActive, autoAllowed, autoOn, spellID = GetPetActionInfo(i)
    out.actions[i] = name and { name = name, token = isToken or nil, active = isActive or nil, spell = spellID,
      autocast = autoAllowed and (autoOn and true or false) or nil } or false
  end
  return out
end

-- Explored areas: each explored world-map overlay, resolved to area IDs by sampling a 3x3 grid inside it.
-- Overlay offsets and sizes are pixels on the map's art layer (as Blizzard's MapExplorationDataProvider
-- draws them), so they are divided by the layer size to get the 0-1 positions the lookup expects.
-- Capital cities have no overlays and are not reported.
function C.Explored()
  local areas, zones, overlays = {}, 0, 0
  for _, continent in ipairs({ 1414, 1415 }) do -- Kalimdor, Eastern Kingdoms
    for _, zone in ipairs(C_Map.GetMapChildrenInfo(continent, Enum.UIMapType.Zone, true) or {}) do
      local layer = (C_Map.GetMapArtLayers(zone.mapID) or {})[1]
      local textures = C_MapExplorationInfo.GetExploredMapTextures(zone.mapID) or {}
      if #textures > 0 then zones = zones + 1 end
      for _, o in ipairs(textures) do
        overlays = overlays + 1
        if layer and layer.layerWidth > 0 and layer.layerHeight > 0 then
          for _, fx in ipairs({ 0.25, 0.5, 0.75 }) do
            for _, fy in ipairs({ 0.25, 0.5, 0.75 }) do
              local x = (o.offsetX + o.textureWidth * fx) / layer.layerWidth
              local y = (o.offsetY + o.textureHeight * fy) / layer.layerHeight
              local ids = C_MapExplorationInfo.GetExploredAreaIDsAtPosition(zone.mapID, CreateVector2D(x, y))
              for _, id in ipairs(ids or {}) do areas[id] = true end
            end
          end
        end
      end
    end
  end
  return { areas = Sorted(areas), zonesWithOverlays = zones, overlays = overlays }
end

-- Localized names of the professions with a recipe window that this character has.
function C.ProfessionNames()
  local have, out = {}, {}
  for _, s in ipairs(CB.char.skills and CB.char.skills.list or {}) do have[s.name] = true end
  for _, id in ipairs(PROFESSION_SPELLS) do
    local name = GetSpellInfo(id)
    if name and have[name] then table.insert(out, name) end
  end
  return out
end
