-- Items: equipped gear, bags, keyring, bank, mail attachments.
--
-- Each item is stored as { s = itemString, n = name, c = count, b = soulbound, d = {cur, max}, ch = charges }.
-- The itemString keeps every ID the server needs: item:itemID:enchantID:gem1:gem2:gem3:gem4:suffixID:uniqueID:...
-- (suffixID is the random property, "of the Bear"). Names are only for humans reading the file.

local _, ns = ...
local CB = ns.CB
local Items = {}
ns.Items = Items

local BANK_BAG_COUNT = 6 -- Era: bank bags are containers 5..10 (Enum.BagIndex has the wrong IDs on Era)

local scanTip = CreateFrame("GameTooltip", "CharSnapshotScanTooltip", nil, "GameTooltipTemplate")
scanTip:SetOwner(WorldFrame, "ANCHOR_NONE")

-- ITEM_SPELL_CHARGES is "%d |4Charge:Charges;" on Classic clients; the |4 sequence picks singular/plural.
local chargePattern = "^" .. ITEM_SPELL_CHARGES:gsub("%%d", "(%%d+)"):gsub("|4[^;]*;", ".-") .. "$"

-- Remaining charges, read from the tooltip (there is no API). Only called for items that have a use spell.
local function Charges(setTooltip)
  scanTip:ClearLines()
  if not pcall(setTooltip, scanTip) then return nil end -- e.g. a slot type the tooltip can't show
  for i = 2, scanTip:NumLines() do
    local line = _G["CharSnapshotScanTooltipTextLeft" .. i]
    local text = line and line:GetText()
    if text then
      if text == ITEM_SPELL_CHARGES_NONE then return 0 end
      local n = text:match(chargePattern)
      if n then return tonumber(n) end
    end
  end
end

local function HasUseSpell(itemID)
  local getSpell = C_Item and C_Item.GetItemSpell or GetItemSpell
  return itemID and getSpell and getSpell(itemID) ~= nil
end

function Items.FromLink(link, count)
  if not link then return nil end
  return {
    s = link:match("|H(item:[^|]+)|h") or link,
    n = link:match("|h%[(.-)%]|h"),
    c = (count and count > 1) and count or nil,
  }
end

local function IsBound(location)
  return C_Item.DoesItemExist(location) and C_Item.IsBound(location) or nil
end

local function ContainerItem(bag, slot)
  local info = C_Container.GetContainerItemInfo(bag, slot)
  if not (info and info.hyperlink) then return nil end
  local item = Items.FromLink(info.hyperlink, info.stackCount)
  item.b = info.isBound or nil
  local cur, max = C_Container.GetContainerItemDurability(bag, slot)
  if max and max > 0 then item.d = { cur, max } end
  if HasUseSpell(info.itemID) then
    item.ch = Charges(function(tip) tip:SetBagItem(bag, slot) end)
  end
  return item
end

-- A bag's contents plus the bag item itself (equipped in inventory slot invSlot).
local function Container(bag, invSlot)
  local size = C_Container.GetContainerNumSlots(bag) or 0
  local out = { size = size, items = {} }
  if invSlot then
    local link = GetInventoryItemLink("player", invSlot)
    if link then
      out.bag = Items.FromLink(link)
      out.bag.b = IsBound(ItemLocation:CreateFromEquipmentSlot(invSlot))
      out.invSlot = invSlot
    end
  end
  for slot = 1, size do
    out.items[slot] = ContainerItem(bag, slot)
  end
  return out
end

-- Inventory slots 1..19 (head..tabard) plus ammo (slot 0). Keyed by the Lua inventory slot.
function Items.Equipped()
  local out = {}
  for slot = 1, 19 do
    local link = GetInventoryItemLink("player", slot)
    if link then
      local item = Items.FromLink(link)
      item.b = IsBound(ItemLocation:CreateFromEquipmentSlot(slot))
      local cur, max = GetInventoryItemDurability(slot)
      if max and max > 0 then item.d = { cur, max } end
      local itemID = GetInventoryItemID("player", slot)
      if HasUseSpell(itemID) then
        item.ch = Charges(function(tip) tip:SetInventoryItem("player", slot) end)
      end
      out[slot] = item
    end
  end
  -- The ammo slot's link is unreliable on Classic; its item ID works. Ammo itself lives in bags.
  local ammo = GetInventoryItemID("player", 0)
  if ammo then out.ammo = ammo end
  if ShowingHelm then out.showHelm = ShowingHelm() and true or false end -- keep false (`false or nil` is nil)
  if ShowingCloak then out.showCloak = ShowingCloak() and true or false end
  return out
end

-- Backpack (0), bags 1..4 and the keyring (-2).
function Items.Bags()
  local out = { [0] = Container(0) }
  for bag = 1, NUM_BAG_SLOTS do
    out[bag] = Container(bag, C_Container.ContainerIDToInventoryID(bag))
  end
  if (GetKeyRingSize and GetKeyRingSize() or 0) > 0 or (C_Container.GetContainerNumSlots(-2) or 0) > 0 then
    out[-2] = Container(-2)
  end
  return out
end

-- Only valid while the bank is open: main bank (-1) and bank bags 5..10.
function Items.Bank()
  local out = { scannedAt = time(), purchasedSlots = GetNumBankSlots(), containers = { [-1] = Container(-1) } }
  for i = 1, BANK_BAG_COUNT do
    out.containers[NUM_BAG_SLOTS + i] = Container(NUM_BAG_SLOTS + i, BankButtonIDToInvSlotID(i, 1))
  end
  return out
end

-- Only valid while the mailbox is open. Never calls GetInboxText (it can mark mail as read).
function Items.Mail()
  local shown, total = GetInboxNumItems()
  local out = { scannedAt = time(), shown = shown, total = total, mails = {} }
  for i = 1, shown do
    local _, _, sender, subject, money, cod, daysLeft, itemCount, wasRead, wasReturned, _, _, isGM =
      GetInboxHeaderInfo(i)
    local mail = {
      sender = sender, subject = subject, money = money, cod = cod, wasReturned = wasReturned or nil,
      isGM = isGM or nil, expires = daysLeft and math.floor(time() + daysLeft * 86400) or nil, items = {},
    }
    if itemCount and itemCount > 0 then
      for a = 1, ATTACHMENTS_MAX_RECEIVE do
        local link = GetInboxItemLink(i, a)
        if link then
          local _, _, _, count = GetInboxItem(i, a)
          mail.items[a] = Items.FromLink(link, count)
        end
      end
    end
    out.mails[i] = mail
  end
  return out
end
