-- Data the client only exposes while an NPC window is open: bank, mailbox, flight master, stable master,
-- profession recipe windows. Also the hearthstone location, which the API only gives as a name.

local _, ns = ...
local CB = ns.CB
local W = {}
ns.Windows = W

local HEARTHSTONE_SPELL = 8690

-- Bank -------------------------------------------------------------------------------------------------
local bankOpen = false
local function ScanBank()
  if not bankOpen then return end
  CB.char.bank = ns.Items.Bank()
end
CB.On("BANKFRAME_OPENED", function() bankOpen = true; CB.Debounce("bank", 0.5, ScanBank) end)
CB.On("BANKFRAME_CLOSED", function() bankOpen = false end)
CB.On("PLAYERBANKSLOTS_CHANGED", function() CB.Debounce("bank", 0.5, ScanBank) end)
CB.On("PLAYERBANKBAGSLOTS_CHANGED", function() CB.Debounce("bank", 0.5, ScanBank) end)
CB.On("BAG_UPDATE_DELAYED", function() if bankOpen then CB.Debounce("bank", 0.5, ScanBank) end end)

-- Mailbox ----------------------------------------------------------------------------------------------
local mailOpen = false
CB.On("MAIL_SHOW", function() mailOpen = true end)
CB.On("MAIL_CLOSED", function() mailOpen = false end)
CB.On("MAIL_INBOX_UPDATE", function()
  if mailOpen then CB.Debounce("mail", 0.5, function() if mailOpen then CB.char.mail = ns.Items.Mail() end end) end
end)

-- Flight master ----------------------------------------------------------------------------------------
-- The flight map lists the nodes of the current continent; "NONE" nodes are undiscovered.
local function Continent(mapID)
  while mapID do
    local info = C_Map.GetMapInfo(mapID)
    if not info then return nil end
    if info.mapType == Enum.UIMapType.Continent then return mapID end
    mapID = info.parentMapID
  end
end

local function ScanTaxi()
  if NumTaxiNodes() == 0 then return end -- map not populated (yet)
  local mapID = C_Map.GetBestMapForUnit("player")
  local continent = Continent(mapID) or mapID or 0
  local known = {}
  local byIndex = {}
  if C_TaxiMap and C_TaxiMap.GetAllTaxiNodes and mapID then
    for _, node in ipairs(C_TaxiMap.GetAllTaxiNodes(mapID) or {}) do
      if node.slotIndex then byIndex[node.slotIndex] = node.nodeID end
    end
  end
  for i = 1, NumTaxiNodes() do
    local kind = TaxiNodeGetType(i)
    if kind ~= "NONE" then
      table.insert(known, { id = byIndex[i], name = TaxiNodeName(i), current = kind == "CURRENT" or nil })
    end
  end
  local taxi = CB.char.taxi or { continents = {}, nodes = {} }
  taxi.continents[continent] = time()
  taxi.nodes[continent] = known
  CB.char.taxi = taxi
end
CB.On("TAXIMAP_OPENED", function() CB.Debounce("taxi", 0.3, ScanTaxi) end)
-- Backup trigger: NPC windows also announce themselves through the interaction manager (6 = flight master).
CB.On("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", function(_, kind)
  if kind == 6 then CB.Debounce("taxi", 0.5, ScanTaxi) end
end)

-- Stable master ----------------------------------------------------------------------------------------
-- GetStablePetInfo's index for the active pet is ambiguous in Blizzard's own code (0 and 1), so every
-- index is stored raw together with the active pet's name.
local function ScanStable()
  local out = { scannedAt = time(), slots = GetNumStableSlots(), active = UnitName("pet"), pets = {} }
  for i = 0, (NUM_PET_STABLE_SLOTS or 2) + 1 do
    local _, name, level, family, loyalty = GetStablePetInfo(i)
    if name then out.pets[tostring(i)] = { name = name, level = level, family = family, loyaltyText = loyalty } end
  end
  CB.char.stable = out
end
CB.On("PET_STABLE_SHOW", function() CB.Debounce("stable", 0.3, ScanStable) end)
CB.On("PET_STABLE_UPDATE", function() CB.Debounce("stable", 0.3, ScanStable) end)

-- Profession windows -----------------------------------------------------------------------------------
-- Recipes are spells; the recipe link carries the spell ID. Lists are merged with earlier scans because
-- the window's filters ("Have Materials", subclass) can hide recipes.
local function LinkSpellID(link)
  return link and tonumber(link:match("enchant:(%d+)") or link:match("spell:(%d+)"))
end

local function StoreRecipes(name, rank, max, recipes)
  if not name or name == "UNKNOWN" then return end
  CB.char.professions = CB.char.professions or {}
  local prof = CB.char.professions[name] or { recipes = {} }
  for id, recipeName in pairs(recipes) do prof.recipes[id] = recipeName end
  prof.rank, prof.max, prof.scannedAt = rank, max, time()
  CB.char.professions[name] = prof
end

local function ScanTradeSkill()
  local name, rank, max = GetTradeSkillLine()
  local recipes = {}
  for i = 1, GetNumTradeSkills() do
    local recipeName, kind = GetTradeSkillInfo(i)
    if kind and kind ~= "header" then
      local id = LinkSpellID(GetTradeSkillRecipeLink and GetTradeSkillRecipeLink(i))
      if id then recipes[id] = recipeName end
    end
  end
  StoreRecipes(name, rank, max, recipes)
end
CB.On("TRADE_SKILL_SHOW", function() CB.Debounce("trade", 0.5, ScanTradeSkill) end)
CB.On("TRADE_SKILL_UPDATE", function() CB.Debounce("trade", 0.5, ScanTradeSkill) end)

-- Enchanting and Beast Training use the older Craft window.
local function ScanCraft()
  local name, rank, max = GetCraftDisplaySkillLine()
  name = name or (GetCraftName and GetCraftName())
  local recipes = {}
  for i = 1, GetNumCrafts() do
    local craftName, subName, kind = GetCraftInfo(i)
    if kind and kind ~= "header" then
      local id = LinkSpellID(GetCraftItemLink(i))
      if id then recipes[id] = subName and subName ~= "" and (craftName .. " (" .. subName .. ")") or craftName end
    end
  end
  StoreRecipes(name, rank, max, recipes)
end
CB.On("CRAFT_SHOW", function() CB.Debounce("craft", 0.5, ScanCraft) end)
CB.On("CRAFT_UPDATE", function() CB.Debounce("craft", 0.5, ScanCraft) end)

-- Hearthstone ------------------------------------------------------------------------------------------
-- GetBindLocation() is only a name. Record coordinates when binding (you stand next to the innkeeper) and
-- right after a hearthstone teleport (you arrive at the bind point).
local function RecordHearth(how)
  local x, y, z, map = UnitPosition("player") -- server position_x, position_y (see Character.Position)
  if not x then return end
  CB.char.hearth = { name = GetBindLocation(), x = x, y = y, z = z, map = map, how = how, at = time(),
    zone = GetRealZoneText(), subzone = GetSubZoneText() }
end

CB.On("HEARTHSTONE_BOUND", function() C_Timer.After(1, function() RecordHearth("bound") end) end)

local hearthing = false
CB.On("UNIT_SPELLCAST_SUCCEEDED", function(_, unit, _, spellID)
  if unit == "player" and spellID == HEARTHSTONE_SPELL then
    hearthing = true
    C_Timer.After(5, function()
      if hearthing then hearthing = false; RecordHearth("teleport") end
    end)
  end
end)
CB.On("PLAYER_ENTERING_WORLD", function()
  if hearthing then
    hearthing = false
    C_Timer.After(2, function() RecordHearth("teleport") end)
  end
end)
