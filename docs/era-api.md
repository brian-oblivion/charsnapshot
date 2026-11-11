# Classic Era API reference for a character-dump addon

Checked 2026-11-10 against:

- **[UI]**: Blizzard UI source, Gethe/wow-ui-source branch `classic_era`, commit `4192ac6` "1.15.9 (70576)".
  Paths below are relative to its `Interface/AddOns/`.
  The client tables from `tools/fetch-data.sh` (`data/era-client.sqlite` `_meta`) report the same build, 1.15.9.70576.
- **[Doc]**: `Blizzard_APIDocumentationGenerated/*.lua` in that tree.
- **[AddOn]**: Era addons installed at `.../_classic_era_/Interface/AddOns/` (Syndicator TOC: Interface 11509).

How to read the "Exists" column:

- **yes (UI)**: Era FrameXML calls the function, so it definitely exists on Era.
- **yes (Doc)**: the function is in the generated docs. Those docs cover all flavours, so this is weaker evidence on its own.
- **yes (AddOn)**: a maintained Era addon calls the function unguarded.
- **uncertain**: none of the three sources settled it.

warcraft.wiki.gg was mostly unavailable. Only `API:GetInboxText` was fetched; bulk fetching was refused.
Anything marked "general knowledge / unverified" should be tested in game.

---

## 0. Patch 1.15.8+ changes that affect every section

1. **Many old globals now exist only as Lua compatibility wrappers.** Each wrapper file starts with
   `if not GetCVarBool("loadDeprecationFallbacks") then return end`. If a user turns that CVar off, these globals are nil.
   Prefer the C_ API in new code.
   - `Blizzard_Deprecated/Deprecated_1_15_8.lua`: `UnitAura`, `UnitBuff`, `UnitDebuff`. These wrap `C_UnitAuras.Get*DataByIndex` and `AuraUtil.UnpackAuraData`.
   - `Blizzard_DeprecatedItemScript/Deprecated_ItemScript.lua`: `GetItemInfo`, `GetItemInfoInstant`, `GetItemCount`, `GetItemCooldown`, `GetItemSpell`, `GetItemIcon`, `GetItemFamily`, `IsEquippedItem`, and others. Use `C_Item.*` and `C_Container.GetItemCooldown` instead.
   - `Blizzard_DeprecatedSpellBook/Deprecated_SpellBook.lua`:
     - `IsPlayerSpell(id)` = `C_SpellBook.IsSpellKnown(id, Enum.SpellBookSpellBank.Player)`
     - `IsSpellKnown(id, isPet)` = `C_SpellBook.IsSpellInSpellBook(id, bank, false)`
   - `Blizzard_DeprecatedSpecialization/Deprecated_Specialization_Vanilla.lua` (TOC `AllowLoadGameType: classic, standard`): `GetTalentInfo`, `GetTalentTabInfo`, `GetActiveTalentGroup`, and others. They wrap `C_SpecializationInfo`.
   - Era FrameXML already calls the C_ versions directly. Example: `Blizzard_FrameXML/Vanilla/TalentFrameBase.lua:91` calls `C_SpecializationInfo.GetTalentInfo`.
2. **`C_TooltipInfo` cannot be used on Era.**
   - `TooltipInfoDocumentation.lua` has an empty `Functions = {}` list.
   - Era FrameXML never calls `C_TooltipInfo.*`.
   - Syndicator, Baganator and Auctionator check for it and fall back to scanning GameTooltip line by line on Era. See Syndicator `Search/BaseInfo.lua:14-22` and `Core/Utilities.lua:71` (`DumpClassicTooltip`).
3. **`Enum.BagIndex` gives the wrong bank bag IDs on Era.**
   - The shared enum says `BankBag_1 = 6`, because `ReagentBag = 5` sits in the way.
   - On Era the bank bags are containers **5–10** (`NUM_BAG_SLOTS + i`).
   - Syndicator `Core/Constants.lua:89-96` says: "Workaround for the enum containing the wrong values for the bank bag slots".
4. **NPC windows open through `PLAYER_INTERACTION_MANAGER_FRAME_SHOW`.** See `Blizzard_UIPanels_Game/Shared/PlayerInteractionFrameManager.lua`.
   - The `Enum.PlayerInteractionType` values are: Merchant 5, TaxiNode 6, Trainer 7, Banker 8, MailInfo 17, Binder 20, StableMaster 22.
   - The old events still exist in the docs, and Syndicator still uses `BANKFRAME_OPENED`. They are `MAIL_SHOW`, `BANKFRAME_OPENED`, `TAXIMAP_OPENED` and `PET_STABLE_SHOW`.

---

## 1. Bags, bank, keyring

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `C_Container.GetContainerNumSlots(bagIndex)` | yes (Doc, UI, AddOn) | → `numSlots` | `ContainerDocumentation.lua`; `Blizzard_UIPanels_Game/Classic/ContainerFrame_Shared.lua:100` | Returns 0 for unequipped bags. Bank containers (-1, 5–10) only read correctly while the bank is open. |
| `C_Container.GetContainerItemInfo(bag, slot)` | yes | → table `ContainerItemInfo` or nothing (empty slot). Fields: `iconFileID, stackCount, isLocked, quality?, isReadable, hasLoot, hyperlink, isFiltered, hasNoValue, itemID, isBound, itemName` | `ContainerDocumentation.lua` (ContainerItemInfo struct) | The link can be incomplete until the item is cached. Syndicator checks `C_Item.IsItemDataCachedByID(itemID)` and otherwise waits on `Item:ContinueOnItemLoad` (`Tracking/BagCache.lua:237-255`). |
| `C_Container.GetContainerItemDurability(bag, slot)` | yes | → `durability, maxDurability` (nothing if the item has no durability) | `ContainerDocumentation.lua`; used by Baganator `ItemViewCommon/Utilities.lua:454` | |
| `C_Container.ContainerIDToInventoryID(bagID)` | yes | → `inventoryID` | Doc; Syndicator `Tracking/EquippedCache.lua:36,89` | `ContainerIDToInventoryID(1)` = 20 on Era, so equipped slots are 0..19. Use `BankButtonIDToInvSlotID(i, 1)` for bank bag slots (Syndicator `BagCache.lua:347`). |
| `C_Container.GetItemCooldown(itemID)` | yes (Doc) | → `startTime, duration, enable` | `ContainerDocumentation.lua` | Global `GetItemCooldown` is only a wrapper now. |
| `BACKPACK_CONTAINER`, `BANK_CONTAINER`, `KEYRING_CONTAINER` | yes | 0, **-1**, **-2** (`Enum.BagIndex.Backpack/Bank/Keyring`) | `Blizzard_FrameXMLBase/Classic/Constants.lua:204-207`; `BagIndexConstantsDocumentation.lua` (Keyring = -2) | **The keyring exists on Era.** Syndicator adds `Enum.BagIndex.Keyring` when `IsEra` (`Core/Constants.lua:81`). |
| `GetKeyRingSize()` | yes (UI) | → number of slots | `Classic/ContainerFrame_Shared.lua:119` | Blizzard sizes the keyring with this function. Syndicator on Era reads it with `C_Container.GetContainerNumSlots(-2)` and only special-cases the Forever client. Both should work; compare the two in game. |
| `NUM_BAG_SLOTS`, `NUM_BANKGENERIC_SLOTS`, `NUM_BANKBAGSLOTS` | yes | `Constants.InventoryConstants.NumBagSlots/NumGenericBankSlots/NumBankBagSlots` | `Classic/Constants.lua:209-211` | The values come from C code. Syndicator hard-codes Era as 4 bags and 6 bank bags (`Constants.lua:85-96`). The 24 generic bank slots are general knowledge. |
| `GetNumBankSlots()` | yes (UI) | → `numPurchasedBagSlots, isFull` | `Vanilla/BankFrame.lua:212` | |
| Bank scan pattern | – | On `BANKFRAME_OPENED`, scan -1 and then 5..(4+NUM_BANKBAGSLOTS). Read bank bag links with `GetInventoryItemLink("player", BankButtonIDToInvSlotID(i,1))`. | Syndicator `Tracking/BagCache.lua:160-172, 337-360` | Bank data is unavailable when the bank is closed, so cache the last scan. |

## 2. Equipped items and item links

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetInventoryItemLink("player", slot)` | yes | → link or nil | Syndicator `EquippedCache.lua:26` | **Ammo slot 0 is unreliable.** ItemRack `ItemRack.lua:701-704` says "classic workaround for ammo slot API bugs" and uses `GetItemInfo(GetInventoryItemID("player",0))` instead. |
| `GetInventoryItemID("player", slot)` | yes | → itemID or nil | Syndicator `EquippedCache.lua:23,89` (scans slots 0..19) | Works for slot 0 (ammo). |
| `GetInventoryItemCount("player", slot)` | yes | → count | `EquippedCache.lua:24` | For slot 0 this is the total ammo count. |
| `GetInventoryItemDurability(slot)` | yes (AddOn) | → `cur, max` or nil | Baganator `ItemViewCommon/Utilities.lua:456` | No unit argument. |
| `C_Item.IsBound(itemLocation)` | yes (Doc, AddOn) | → bool | `ItemDocumentation.lua`; Syndicator `EquippedCache.lua:28` with `{equipmentSlotIndex = slot}` and `BagCache.lua:329` | **This is how to tell soulbound for equipped items.** Build the location with `ItemLocation:CreateFromEquipmentSlot(slot)` or `CreateFromBagAndSlot(bag, slot)`, and check `C_Item.DoesItemExist(loc)` first. |
| `C_Item.GetItemInfo` / `GetItemInfoInstant` / `IsItemDataCachedByID` / `RequestLoadItemDataByID` | yes (Doc) | standard | `ItemDocumentation.lua` | The globals are wrappers (section 0). |
| Item link / itemString layout | yes | `item:itemID:enchantID:gem1:gem2:gem3:gem4:suffixID:uniqueID:linkLevel:specID:modifiersMask:itemContext:numBonusIDs[:bonusIDs…]` | Syndicator `Search/GetGroupingKey.lua:19-23`. After `strsplit(":")` on a full link, entries 9–13 are "uniqueID, linkLevel, specializationID, modifiersMask, itemContext". The gem regex in `Search/CheckItem.lua:188` and ItemRack `ItemRack.lua:669-675` agree. | Era has no gems, so gem fields are empty. `suffixID` is the random property ("of the Bear"); see `ItemRandomProperties` in the client DB. `linkLevel` is the player's level at query time, so strip it when comparing (ItemRack `UpdateIRString`). |
| Item charges | **no API** | Scan the tooltip. Use `GameTooltip:SetBagItem(bag,slot)` or `SetInventoryItem("player",slot)` and match `ITEM_SPELL_CHARGES` / `ITEM_SPELL_CHARGES_NONE`. | Baganator `ItemViewCommon/Utilities.lua:398-432` (pattern build), used with `DumpClassicTooltip` on non-retail. On Classic `ITEM_SPELL_CHARGES` contains a `|4singular:plural;` sequence, which Baganator expands. | `C_Item.GetItemCount(item, includeBank, includeUses)` has an `includeUses` argument (Doc), but whether it counts charges on Era is **unverified**. `C_TooltipInfo` is unavailable (section 0). |

## 3. Mail

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetInboxNumItems()` | yes (UI) | → `numItems, totalItems` | `Classic/MailFrame.lua:156` | `totalItems > numItems` means more mail is waiting on the server than the client has loaded. You only see the loaded mail. |
| `GetInboxHeaderInfo(i)` | yes (UI) | → `packageIcon, stationeryIcon, sender, subject, money, CODAmount, daysLeft, itemCount, wasRead, wasReturned, textCreated, canReply, isGM, firstItemQuantity, firstItemID` | `Classic/MailFrame.lua:174, 383, 487` | `daysLeft` is fractional (Syndicator converts it to an expiry time). |
| `GetInboxItem(i, a)` | yes | → `name, itemID, texture, count, quality, canUse, isCurrency?` | `MailFrame.lua:433`; Syndicator `MailCache.lua:31` | Loop `a = 1, ATTACHMENTS_MAX_RECEIVE`. |
| `GetInboxItemLink(i, a)` | yes (AddOn) | → link | Syndicator `MailCache.lua:35` | Wait for item cache as for bags. |
| `ATTACHMENTS_MAX_RECEIVE` | yes | **16** (`ATTACHMENTS_MAX = 16`, `ATTACHMENTS_MAX_SEND = 12`) | `Classic/MailFrame.lua:3-7` | |
| `GetInboxText(i)` | yes | → `bodyText, stationaryMiddle, stationaryEdge, isTakeable, isInvoice` | wiki `API:GetInboxText` (fetched); `MailFrame.lua:502` | **Don't call it.** Whether it marks mail as read is **unverified**: the wiki page doesn't say so, and it's only commonly reported. Syndicator never calls it and reads only header and item data. |
| Events | yes (Doc) | `MAIL_SHOW`, `MAIL_INBOX_UPDATE`, `MAIL_CLOSED`, `PLAYER_INTERACTION_MANAGER_FRAME_SHOW` (type 17) | `MailInfoDocumentation.lua`; `MailFrame.lua:26,102` (`CheckInbox()` on show) | Scan on `MAIL_INBOX_UPDATE` after the mailbox opens; Syndicator `MailCache.lua:52,153` does this. Mail is readable only while the mailbox is open. |

## 4. Spells

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetNumSpellTabs()` | yes (UI) | → n | `Vanilla/SpellBookFrame.lua:38` | |
| `GetSpellTabInfo(tab)` | yes (UI) | → `name, texture, offset, numSlots, isGuild, offSpecID, shouldHide, specID` | `Classic/SpellBookFrame.lua:473`, `Vanilla/SpellBookFrame.lua:212` | Tab 1 is "General". The spellbook slot index is `offset + j` for `j = 1..numSlots`. |
| `GetSpellBookItemInfo(slot, BOOKTYPE_SPELL)` | yes (UI) | → `slotType, id`. slotType is "SPELL", "FUTURESPELL", "PETACTION" or "FLYOUT"; id is the spellID | `Vanilla/SpellBookFrame.lua:189,312`; `Classic/SpellBookFrame.lua:422-433` | `BOOKTYPE_SPELL = "spell"`, `BOOKTYPE_PET = "pet"` (`Classic/SpellBookFrame.lua:6,8`). `C_SpellBook.GetSpellBookItemInfo` is **not** in the Era docs. |
| `GetSpellBookItemName(slot, bookType)` | yes (UI) | → `name, subName, spellID` | `Vanilla/SpellBookFrame.lua:261` | The rank text ("Rank 3") is available through `Spell:CreateFromSpellID(id):GetSpellSubtext()` (line 268). |
| **Rank enumeration** | – | – | `Vanilla/SpellBookFrame.xml:256-272` (`ShowAllSpellRanksCheckbox` ↔ CVar `ShowAllSpellRanks`). WeakAuras' `LibDispel.lua:1306-1307` comments "this will fix a problem where spells dont show as existing because they are 'hidden'", then sets the CVar to 1 temporarily and restores it. | **The spellbook lists lower ranks only when CVar `ShowAllSpellRanks` = 1.** The checkbox is hidden for Rogue and Warrior. Either set the CVar to 1, scan, and restore it (as LibDispel does; this fires `CVAR_UPDATE`, which `LibSpellRange` listens for), or check every rank ID with `IsPlayerSpell`. |
| `IsPlayerSpell(id)` | yes (wrapper) | → bool; it is `C_SpellBook.IsSpellKnown(id, Player)` | `Blizzard_DeprecatedSpellBook/Deprecated_SpellBook.lua:11-14`; RXPGuides `RXPGuides.lua:272-273` | Doc: "can also return true for spells that aren't in the spellbook". Best choice for talent-granted and lower-rank spells. **Profession recipe spells: unverified, test in game.** |
| `IsSpellKnown(id[, isPet])` | yes (wrapper) | → bool; it is `C_SpellBook.IsSpellInSpellBook(id, bank, false)` | same file:16-20 | Depends on what the spellbook shows, so it is affected by `ShowAllSpellRanks`. RXP uses `IsSpellKnown` to detect professions (`RXPGuides.lua:332`). |
| `C_SpellBook.IsSpellKnown`, `IsSpellInSpellBook`, `HasPetSpells` | yes (Doc) | see above | `SpellBookDocumentation.lua` | |
| `GetSpellInfo(id)` (global) | yes (UI) | → `name, rank/subtext, icon, castTime, minRange, maxRange, spellID` | `Classic` FrameXML: `local name, subname = GetSpellInfo(...)`. Not in the deprecation lists, so it is still native. `C_Spell.GetSpellInfo` → table `{name, iconID, castTime, minRange, maxRange, spellID, originalIconID}` (Doc). | |

## 5. Talents

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetNumTalentTabs([isInspect, isPet])` | yes (UI) | → 3 | `Blizzard_FrameXML/Classic/TalentFrameBase_Shared.lua:327` | `MAX_TALENT_TABS = 3`, `MAX_NUM_TALENTS = 40` (`Blizzard_FrameXML/Vanilla/TalentFrameBase.lua:2-4`). |
| `GetNumTalents(tab[, isInspect, isPet])` | yes (UI) | → n | `Vanilla/TalentFrameBase.lua:62` | |
| **`C_SpecializationInfo.GetTalentInfo(query)`** | yes (UI) | query `{specializationIndex=tab, talentIndex=i, isInspect=false, isPet=false, groupIndex?}` → table `{talentID, name, icon, tier, column, rank, maxRank, meetsPrereq, previewRank, …, spellID, known, selected}` or nil | `SpecializationInfoDocumentation.lua`; `Vanilla/TalentFrameBase.lua:84-91` | **This returns a talent ID.** `talentID` is the `Talent.db2` ID; the client DB `Talent` table has `TabID, TierID, ColumnIndex, SpellRank_0..8`. Details uses `talentInfo.talentID` on Vanilla (`Libs/PlayerInfo/PlayerInfo.lua:1281-1300`). Store `talentID` and `rank`, not the index. The index order (1..n) comes from the client and nothing documents it, so don't rely on it. Tier and column from the API are 1-based (luaIndex); the db2 values are probably 0-based. |
| `GetTalentInfo(tab, i)` | yes (wrapper) | → `name, icon, tier, column, rank, maxRank, meetsPrereq, previewRank, meetsPreviewPrereq, isExceptional, hasGoldBorder, talentID` | `Deprecated_Specialization_Vanilla.lua` | Old addons still call it on Era (WeakAuras `Compatibility.lua:126`, PallyPower). |
| `GetTalentTabInfo(tab)` | yes (wrapper) | → **`specId, name, description, icon, pointsSpent, background, previewPointsSpent, isUnlocked`** | `Deprecated_Specialization_Vanilla.lua:10-15`; DBM-Core `DBM-Core.lua:3514` reads `pointsSpent` as the 5th return | **The return order differs from the 2019-era Classic API** (`name, icon, pointsSpent, background`). Better: `C_SpecializationInfo.GetSpecializationInfo(tab)` → `specId, name, description, icon, role, primaryStat, pointsSpent, background, previewPointsSpent, isUnlocked`. |
| `GetUnspentTalentPoints(isInspect, isPet, group)` / `UnitCharacterPoints("player")` | yes (UI) | → n | `Vanilla/TalentFrameBase.lua:301`; `Blizzard_TrainerUI.lua:539` | |

## 6. Skills

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetNumSkillLines()` | yes (UI) | → n (includes header rows) | `Classic/SkillFrame.lua:290` | |
| `GetSkillLineInfo(i)` | yes (UI) | → `skillName, isHeader, isExpanded, skillRank, numTempPoints, skillModifier, skillMaxRank, isAbandonable, stepCost, rankCost, minLevel, skillCostType, skillDescription` | `Classic/SkillFrame.lua:26,183` | **The name is localized and there is no skill-line ID.** Map the name to `SkillLine.DisplayName_lang` (client DB) offline, as Questie does with its `professionTable[skillName]` (`Modules/QuestieProfessions.lua:74-76`). |
| `ExpandSkillHeader(i)` / `CollapseSkillHeader(i)` | yes (UI) | `ExpandSkillHeader(0)` expands all | `Classic/SkillFrame.xml:72,78`; Questie `QuestieProfessions.lua:62` | Rows under a collapsed header are hidden. Expanding changes the user's UI state. |

## 7. Reputation

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetNumFactions()` | yes (UI) | → n (visible rows) | `Vanilla/ReputationFrame.lua:24` | |
| `GetFactionInfo(i)` | yes (UI) | → `name, description, standingID, barMin, barMax, barValue, atWarWith, canToggleAtWar, isHeader, isCollapsed, hasRep, isWatched, isChild, **factionID** (14th), hasBonusRepGain, canBeLFGBonus` | `Vanilla/ReputationFrame.lua:40` | Yes, `factionID` is the 14th return. `barValue` is total reputation; `standingID` runs 1..8. |
| `GetFactionInfoByID(factionID)` | yes (UI) | same returns | Era UI uses it once; Questie `QuestieReputation.lua:267,359` | Lets you query known faction IDs (`Faction` table in the client DB) without walking headers. |
| `ExpandFactionHeader(i)` / `CollapseFactionHeader(i)` | yes (UI) | `ExpandFactionHeader(0)` = expand all | `Vanilla/ReputationFrame.xml:10,12`; Questie `QuestieReputation.lua:25` "Expand all header" | |
| `C_Reputation` | partly | Era docs have only paragon/watched/guild helpers. **No `GetFactionDataByIndex` or `GetNumFactions`.** | `ReputationInfoDocumentation.lua`; WeakAuras `Compatibility.lua:131` falls back to `GetNumFactions` | Use the legacy globals. |

## 8. Quests

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetQuestsCompleted()` | yes (AddOn) | → table `{[questID]=true}` | Questie `Modules/Quest/QuestieQuest.lua:73` (called directly at init) | |
| `C_QuestLog.GetAllCompletedQuestIDs` | **no** (not in Era docs) | – | `QuestLogDocumentation.lua` function list | Use `GetQuestsCompleted()`. |
| `C_QuestLog.IsQuestFlaggedCompleted(id)` | yes (Doc, AddOn) | → bool | Doc; RXP `functions.lua:346` | |
| `GetNumQuestLogEntries()` | yes (UI) | → `numEntries, numQuests` | `Vanilla/QuestLogFrame.lua:113` | Entries include headers. |
| `GetQuestLogTitle(i)` | yes (UI) | → `title, level, questTag, isHeader, isCollapsed, isComplete, frequency, **questID** (8th), startEvent, displayQuestID, isOnMap, hasLocalPOI, isTask, isBounty, isStory, isHidden, isScaling` | `Vanilla/QuestLogFrame.lua:150`; `Vanilla/QuestInfo.lua:145` uses `select(8, …)` | `isComplete` is 1 (complete), -1 (failed) or nil. Expand with `ExpandQuestHeader(0)` (`QuestLogFrame.lua:595`). |
| `GetNumQuestLeaderBoards(i)` / `GetQuestLogLeaderBoard(j, i)` | yes (UI) | → `text, type, finished` | `QuestLogFrame.lua:391`; RXP `functions.lua:737` (two-argument form) | The text is localized ("Name: x/y", built from `QUEST_MONSTERS_KILLED`/`QUEST_ITEMS_NEEDED`; see Questie `QuestieLib.TrimObjectiveText`). Type is "monster", "item", "object", "event" or "reputation". |
| `C_QuestLog.GetQuestObjectives(questID)` | yes (Doc, AddOn) | → `{ {text, type, finished, numFulfilled, numRequired, objectiveType?}, … }` | Doc; Questie `Modules/Quest/QuestLogCache.lua:100` | **Use this for numbers.** Questie notes that `text` can be blank or start with a space until cached (`QuestLogCache.lua:105,152-168`), and that counts temporarily go down during zone transitions (`:117-125`). |
| `GetQuestLogTimeLeft()` | yes (UI) | → seconds or nil, **for the selected quest** | `QuestLogFrame.lua:106,370`; Questie `Tracker/TrackerQuestTimers.lua:103-110` | Save `GetQuestLogSelection()`, call `SelectQuestLogEntry(i)`, read the timer, then restore the selection. |

## 9. Taxi (flight paths)

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `NumTaxiNodes()`, `TaxiNodeName(i)`, `TaxiNodeGetType(i)`, `TaxiNodePosition(i)`, `TaxiNodeCost(i)` | yes (UI) | type is "CURRENT", "REACHABLE", "DISTANT" or "NONE"; position → `x, y` (0–1, taxi-map space) | `Classic/TaxiFrame.lua:50-80,134-158`; NWB `NovaWorldBuffs.lua:9659` ("CURRENT") | Only valid while the flight map is open (`TAXIMAP_OPENED` .. `TAXIMAP_CLOSED`). Blizzard skips "NONE" nodes, which in practice means not discovered (general knowledge). The name is localized. |
| `C_TaxiMap.GetAllTaxiNodes(uiMapID)` | yes (Doc, AddOn) | → `{ {nodeID, position, name, state (Enum.FlightPathState Current=0/Reachable=1/Unreachable=2), slotIndex, textureKitPrefix?} }` | `TaxiMapDocumentation.lua` ("at the current flight master"); RXP `Timers.lua:157-181` passes `C_Map.GetBestMapForUnit("player")` and keeps `state ~= Unreachable` | **Gives `nodeID` (TaxiNodes.db2 ID) and `slotIndex`** (= index for the `TaxiNode*` functions). Join on slotIndex, then use `TaxiNodeGetType(slotIndex) ~= "NONE"` as "known". Whether unknown nodes come back as Unreachable is **unverified**. |
| `C_TaxiMap.GetTaxiNodesForMap(uiMapID)` | yes (Doc, AddOn) | → `{ {nodeID, position, name, atlasName, faction, textureKitPrefix?, isUndiscovered} }` | Doc; NWB `NovaWorldBuffs.lua:9637-9666` (continent maps 1414/1415) | Works without a flight master. Whether `isUndiscovered` is correct for the player on Era is **unverified**. Era's world map doesn't use this provider (`Blizzard_WorldMap/Vanilla/Blizzard_WorldMap.lua:185`, commented out). Test it before relying on it. |
| Known flight paths without a flight master | **probably not** | – | – | The client has no API for the known-node bitmask (TaxiNodes `CharacterBitNumber` is server state). RXP and NWB both record the list on `TAXIMAP_OPENED` and cache it (`RXPCData.flightPaths[id]`). The `isUndiscovered` field above is the only possible way. |

## 10. Exploration and maps

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `C_MapExplorationInfo.GetExploredAreaIDsAtPosition(uiMapID, CreateVector2D(x,y))` | yes (Doc, AddOn) | → table of areaIDs, or nil if unexplored | Doc; Questie `Modules/Map/QuestieMapUtils.lua:63-82` | x and y are 0–1. **Capital cities return nil.** Questie hard-codes maps 1453–1458 as explored. |
| `C_MapExplorationInfo.GetExploredMapTextures(uiMapID)` | yes (Doc, UI) | → `{ {textureWidth, textureHeight, offsetX, offsetY, isShownByMouseOver, isDrawOnTopLayer, fileDataIDs, hitRect} }` | `Blizzard_SharedMapDataProviders/MapExplorationDataProvider.lua:91` (loaded on vanilla) | Returns **explored overlays only**, with no areaID. To get areaIDs, call `GetExploredAreaIDsAtPosition` at each `hitRect` centre. Event: `MAP_EXPLORATION_UPDATED`. |
| `C_Map.GetMapInfo(id)` | yes | → `{mapID, name, mapType, parentMapID}` | `MapDocumentation.lua` | |
| `C_Map.GetMapChildrenInfo(id[, mapType, allDescendants])` | yes | → list of UiMapDetails | Doc | |
| `C_Map.GetBestMapForUnit("player")`, `C_Map.GetPlayerMapPosition(map, "player")` | yes | → uiMapID; → Vector2D or nil | Doc ("Only works for the player and party members") | |

## 11. Position and bind

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `UnitPosition("player")` | yes (UI, AddOn) | → `posX, posY, z, instanceID` in **server** world coordinates: the 1st value is the emulators' `position_x` (north), the 2nd their `position_y` (west). HereBeDragons names them `y, x` because it uses map orientation (its x runs east = −server Y). **The 3rd value (z) is always 0 on Era** (seen in game 2026-11-10), so the height must come from elsewhere (CharSnapshot's importer uses the nearest spawn / innkeeper). Checked 2026-11-10: HBD `GetWorldCoordinatesFromZone` (x = left − width·mapX) and Auberdine (TaxiNodes Pos 6341, 557; vmangos innkeeper 6406, 515). | HereBeDragons in Questie/TomTom/GatherMate2 (`HereBeDragons-2.0.lua:603,615`); PTR reporter uses `select(4, UnitPosition(...))` as mapID | HBD: "the same restrictions as for the UnitPosition() API apply". On retail it returns nil inside instances; **Era behaviour is unverified.** `instanceID` is the continent or instance map ID. |
| `GetPlayerFacing()` | yes (AddOn) | → radians | TomTom `TomTom_CrazyArrow.lua:155` | Possibly nil in instances (retail rule, unverified on Era). |
| `GetBindLocation()` | yes (Doc, UI) | → localized area **name only** | `PlayerScriptDocumentation.lua`; StaticPopup use | No coordinates. Use your own name→area table. |
| `GetRealZoneText()`, `GetSubZoneText()`, `GetZoneText()`, `GetMinimapZoneText()` | yes (Doc) | → localized strings | `ZoneScriptDocumentation.lua` | |
| Bind events | yes (Doc) | `CONFIRM_BINDER(areaName)` (innkeeper asks); `HEARTHSTONE_BOUND` (after binding) | `UnitDocumentation.lua` | **`PLAYER_BINDING_CHANGED` does not exist** in the Era docs. Re-read `GetBindLocation()` on `HEARTHSTONE_BOUND`. |

## 12. Player

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `UnitRace("player")` | yes | → `localizedName, raceFile, **raceID**` | PTR reporter `Blizzard_PTRFeedback/Blizzard_Reports.lua:214` uses `select(3, UnitRace(...))` | |
| `UnitClass` | yes (Doc) | → `className, classFilename, classID` | `UnitDocumentation.lua` | |
| `UnitSex` | yes | → 2 = male, 3 = female (legacy numbering; general knowledge) | Doc says Nilable number. `Enum.UnitSex` (Male=0, Female=1) is a different enum. | Don't mix the two. |
| `UnitXP`, `UnitXPMax`, `GetXPExhaustion()` (nil when not rested), `GetRestState()`, `GetMoney()` (copper), `UnitLevel` | yes | standard | Doc and UI | |
| `RequestTimePlayed()` + `TIME_PLAYED_MSG(totalTimePlayed, timePlayedThisLevel)` | yes (Doc) | – | `SystemDocumentation.lua:178`; `Blizzard_ChatFrameBase/Classic/ChatFrameOverrides.lua:115-117` calls `ChatFrameUtil.DisplayTimePlayed` | **Suppression changed in 1.15.8+.** Chat now calls `ChatFrameUtil.DisplayTimePlayed`; the global `ChatFrame_DisplayTimePlayed` is only an alias (`Deprecated_ChatFrame.lua:66`). DeathNotificationLib (`DeathNotificationLib~Events.lua:42-56,68-71`) overrides `ChatFrameUtil.DisplayTimePlayed` with a counter. Its CHANGELOG V6 says every chat frame gets the event, so decrement the counter on the next frame (`C_Timer.After(0)`). RareScanner's LibTime instead unregisters `TIME_PLAYED_MSG` from the ChatFrames temporarily. |
| `UnitGUID("player")` | yes | `Player-serverID-dbID` | Doc; DBM `GetCIDFromGUID` (`DBM-Core.lua:3115-3128`) | |
| `GetGuildInfo("player")` | yes (UI) | → `guildName, guildRankName, guildRankIndex[, realm]` | Era UI (`local guildName, guildRankName, guildRankIndex = GetGuildInfo("player")`) | nil until guild data loads (`PLAYER_GUILD_UPDATE`). |
| `GetBuildInfo()` | yes | 4th return = interface number (e.g. 11509) | Syndicator `Core/Constants.lua:4` `select(4, GetBuildInfo())` | |

## 13. Honor (pre-TBC ranking)

All signatures below come from `Blizzard_UIPanels_Game/Classic/HonorFrame_Shared.lua:36-92` (loaded on Era).

| API | Exists | Returns | Gotchas |
|---|---|---|---|
| `GetPVPLifetimeStats()` | yes (UI) | `hk, dk, highestRank` | Pass `highestRank` to `GetPVPRankInfo`. |
| `GetPVPYesterdayStats()` | yes | `hk, dk, contribution` | |
| `GetPVPThisWeekStats()` | yes | `hk, contribution` | |
| `GetPVPLastWeekStats()` | yes | `hk, dk, contribution, rank` | |
| `GetPVPSessionStats()` | yes | `hk, dk` | |
| `UnitPVPRank("player")` | yes | rank index | Pass it to `GetPVPRankInfo`. The 0 / 5..18 raw range is general knowledge. |
| `GetPVPRankInfo(rank)` | yes | `rankName, rankNumber` | `rankNumber` is 1..14 and selects the badge `PvPRank%02d`. The name is localized. |
| `GetPVPRankProgress()` | yes | 0–1 progress toward the next rank | |
| Events | yes (Doc) | `PLAYER_PVP_KILLS_CHANGED`, `PLAYER_PVP_RANK_CHANGED` | |

## 14. Hunter pet and stable

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `UnitExists("pet")`, `HasPetUI()` → `hasUI, isHunterPet` | yes (UI) | | `Classic/PetStable.lua:44` | |
| `UnitGUID("pet")` | yes | `Pet-0-server-instance-zoneUID-**npcID**-spawnUID` | DBM `DBM-Core.lua:3123` and GTFO `GTFO.lua:187` parse field 6 as the creature ID for type "Pet" | That this is the tamed beast's creature entry is high confidence but **unverified**; test with a known pet. `UnitCreatureID` is in the Doc, but its existence on Era is uncertain. |
| `UnitCreatureFamily("pet")` | yes (UI) | → **localized** family name | `Vanilla/PetPaperDollFrame.lua:62-67` (compares to "Felguard" in enUS) | Use the client DB `CreatureFamily` table to map it, or read the icon. |
| `GetPetHappiness()` | yes (UI) | → `happiness (1-3), damagePercentage, loyaltyRate` | `Blizzard_UnitFrame/Classic/PetFrame.lua:169` | |
| `GetPetLoyalty()` | yes (UI) | → **localized string** ("Loyalty Level N (Name)") | `PetPaperDollFrame.lua:73` | Parse the digits out of the string. |
| `GetPetTrainingPoints()` | yes (UI) | → `totalPoints, spent` | `Blizzard_CraftUI/Vanilla/Blizzard_CraftUI.lua:372` | Event: `UNIT_PET_TRAINING_POINTS`. |
| `GetPetExperience()` | yes (UI) | → `currXP, nextXP` | `PetPaperDollFrame.lua:170` | Event: `UNIT_PET_EXPERIENCE`. |
| `HasPetSpells()` + `GetSpellBookItemInfo(i, BOOKTYPE_PET)` | yes (UI) | → `numPetSpells, petToken` | `Vanilla/SpellBookFrame.lua:66`; `Classic/SpellBookFrame.lua:449` | Iterate `i = 1..numPetSpells`. |
| `GetPetActionInfo(i)` (i = 1..10) | yes (UI) | → `name, texture, isToken, isActive, autoCastAllowed, autoCastEnabled, spellID, checksRange, inRange` | Era pet action bar | If `isToken`, the name is a global string key. |
| `GetNumStableSlots()` | yes (UI) | → purchased slots (Era UI has `NUM_PET_STABLE_SLOTS = 2`) | `Classic/PetStable.lua:1,74` | |
| `GetStablePetInfo(index)` | yes (UI) | → `icon, name, level, family, loyalty` (5th return is the localized loyalty text) | `Classic/PetStable.lua:81,148,176` | **Index meaning:** stabled slot *k* is `GetStablePetInfo(k+1)`. Blizzard's own code reads the current pet as both index 0 (`:148`) and index 1 (`:145,176`), so test both. Only valid during `PET_STABLE_SHOW`..`PET_STABLE_CLOSED` (+`PET_STABLE_UPDATE`). `C_StableInfo.GetNumStablePets()` also exists (`:75`). |

## 15. Action bars and macros

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `GetActionInfo(slot)` | yes (UI) | → `actionType, id, subType`. Types: "spell", "item", "macro", "companion", "equipmentset", "flyout" | `Blizzard_ActionBar/Shared/ActionButton.lua:1030-1037` | **The macro `id` is ambiguous.** The shared ActionButton code treats it as the macro's current spell ID when `subType == "spell"` (`:880,1033`), but RXPGuides on Era treats it as the macro index (`Tips.lua:337-341`, `GetMacroSpell(id)`). Use `GetActionText(slot)` (macro name) together with `GetMacroIndexByName` to identify the macro. Test in game. |
| Slot range | likely 1..120 | 6 pages × 12 (`NUM_ACTIONBAR_PAGES = 6`, `Shared/ActionButtonUtil.lua:2-3`) plus bonus/stance bars up to 120 | | The 120 upper bound is general knowledge. Loop to 180 with `HasAction(i)` once to confirm. |
| `GetNumMacros()` | yes (UI) | → `numAccount, numCharacter` | `Blizzard_MacroUI` | Character macros start at index `MAX_ACCOUNT_MACROS + 1`. |
| `GetMacroInfo(index)` | yes (UI) | → `name, iconTexture, body` | `Blizzard_MacroUI.lua` | |
| `MAX_ACCOUNT_MACROS`, `MAX_CHARACTER_MACROS` | yes | **120 / 30** | `Blizzard_UIParent/Vanilla/UIParent.lua:18-19` | |

## 16. Auras and cooldowns

| API | Exists | Signature / returns | Source | Gotchas |
|---|---|---|---|---|
| `C_UnitAuras.GetAuraDataByIndex(unit, i, filter)` | yes (Doc) | → AuraData `{name, icon, applications, dispelName, duration, expirationTime, sourceUnit, isStealable, spellId, isFromPlayerOrPlayerPet, timeMod, points, auraInstanceID, …}` | `UnitAuraDocumentation.lua`; field list from `Blizzard_FrameXMLUtil/AuraUtil.lua:33-54` | `expirationTime` uses the `GetTime()` clock. Convert it to remaining seconds (`expirationTime - GetTime()`) before saving. 0 means no expiry. `C_UnitAuras.GetBuffDataByIndex` and `GetDebuffDataByIndex` also exist. |
| `UnitAura` / `UnitBuff` / `UnitDebuff` | wrapper only | old 16-value form | `Blizzard_Deprecated/Deprecated_1_15_8.lua` | WeakAuras rebuilds them if nil (`AuraEnvironment.lua:14-18`). |
| `GetSpellCooldown(spellID or slot, bookType)` | yes (UI) | → `start, duration, enabled` | `Classic/SpellBookFrame.lua` | `C_Spell.GetSpellCooldown(id)` → `{startTime, duration, isEnabled, isActive?, modRate, activeCategory?}` (Doc). |
| `C_Container.GetItemCooldown(itemID)`, `GetInventoryItemCooldown("player", slot)` | yes | → `start, duration, enable` | Doc; Era UI | Times are on the `GetTime()` clock. |

## 17. Friends and ignore

| API | Exists | Returns | Source | Gotchas |
|---|---|---|---|---|
| `C_FriendList.GetNumFriends()` | yes (Doc) | n | `FriendListDocumentation.lua` (`RequiresFriendList`) | Empty until the server sends the list. Call `C_FriendList.ShowFriends()` and wait for `FRIENDLIST_UPDATE`. |
| `C_FriendList.GetFriendInfoByIndex(i)` | yes | `{connected, name, className?, area?, notes?, guid, level, dnd, afk, rafLinkType}` | Doc | `className` is localized. |
| `C_FriendList.GetNumIgnores()` / `GetIgnoreName(i)` | yes | n / name | Doc | Event: `IGNORELIST_UPDATE`. |

## 18. Hardcore and self-found

| API | Exists | Returns | Source | Gotchas |
|---|---|---|---|---|
| `C_GameRules.IsHardcoreActive()` | yes (Doc, UI) | bool | Era UI `Blizzard_UIParent/Vanilla/UIParent.lua:386,627,1713`; Questie `VersionCheck.lua:105` | True on Hardcore realms. |
| `C_GameRules.IsGameRuleActive(Enum.GameRule.HardcoreRuleset / PvPRuleset / RPRuleset)` | yes (AddOn) | bool | NovaWorldBuffs `Modules/DB.lua:36-42`; Syndicator `Core/Utilities.lua:33-37` | |
| `C_Seasons.GetActiveSeason()` | yes (Doc) | `Enum.SeasonID`: NoSeason 0, SeasonOfMastery 1, SeasonOfDiscovery 2, **Hardcore 3**, Fresh 11, **FreshHardcore 12** | `SeasonsConstantsDocumentation.lua` | |
| **How Deathlog detects Hardcore** | – | `IsHardcoreActive() or C_ClassicHardcore.IsHardcoreSelf() or GetActiveSeason() == 3 or GetActiveSeason() == 12` | `DeathNotificationLib/DeathNotificationLib~UnitState.lua:32` | `C_ClassicHardcore` is not in the Era docs; it is guarded. |
| `C_GameRules.IsSelfFoundAllowed()` | yes (Doc) | bool. Realm allows self-found; only the **glue** UI uses it (`GlueXML/Vanilla/CharacterCreate.lua:239`) | | This is a realm rule, not a character flag. |
| `C_PlayerInfo.IsSelfFoundActive()` | uncertain (Doc only) | bool | `PlayerInfoDocumentation.lua` | No Era UI or addon calls it. Fallback: check for the buff spell **431567 "Self-Found Adventurer"** (client DB `SpellName`) with `C_UnitAuras.GetPlayerAuraBySpellID(431567)`. |
| "Permanently dead" | **no API found** | – | – | Character select shows a death icon (glue only, `GlueXML/Classic/CharacterSelect.lua:1130`). In game, combine `IsHardcoreActive()` with `UnitIsDeadOrGhost("player")`. The event `HARDCORE_DEATHS(memberName)` reports other players' deaths (`DeathAlertDocumentation.lua`). |

## 19. Appearance

| API | Exists | Notes |
|---|---|---|
| `C_BarberShop.GetAvailableCustomizations()` | yes (Doc), but **only at a barber chair** | `MayReturnNothing`. The PTR reporter reads it only between BarberShopOpened and BarberShopClosed (`Blizzard_PTRFeedback/Blizzard_Reports.lua:217-255`). `Blizzard_BarbershopUI` is LoadOnDemand on Classic. It is uncertain whether Era (non-SoD) realms have usable barber chairs. |
| `C_PlayerInfo.GetPlayerCharacterData()` | yes (Doc) | Returns only `{name, fileName, alternateFormRaceData?, createScreenIconAtlas, sex}`, with no skin, face or hair fields. |
| `C_PlayerInfo.GetDisplayID()` / `GetNativeDisplayID()` | yes (Doc) | The model display ID, not the customization choices. |
| **Conclusion** | | **No in-world API returns skin, face, hair, hair colour or facial hair outside the barber shop.** |

## 20. SavedVariables and lifecycle

All of these are **general knowledge / unverified**; none of the sources covers them.

- **Registering an event the client doesn't have raises an error** and aborts loading the rest of that file
  (verified in game 2026-11-10: `LEARNED_SPELL_IN_TAB` doesn't exist on Era 1.15.9, so CharSnapshot 0.2.0's
  slash command never got registered). Only use events in the Era API docs, or guard with
  `C_EventUtils.IsEventValid(event)` as RXPGuides does. `tools/char_snapshot.py lint` checks the addon.
- **Don't read character data at `PLAYER_LOGOUT`** (verified in game 2026-11-10): by then the client reports empty
  bags (0 slots), no equipped items, 0 money, nameless 0/0 skills, no action buttons and no completed quests.
  Talents, spells and reputation still read fine. Scan earlier and treat `PLAYER_LEAVING_WORLD` as the end.
- **Write timing.** SavedVariables are written at logout, `/reload` or a clean quit. `PLAYER_LOGOUT` is the last event, and whatever is in the table at the end of its handlers gets saved. A crash or Alt-F4 loses everything since the last save. Do the heavy scanning earlier, when events arrive, and keep `PLAYER_LOGOUT` light.
- **Size.** There is no documented hard size limit. Very large files slow down login and reload, because the whole file is parsed at load.
- **Content.** Store only strings, numbers, booleans and tables. Functions and userdata are not saved. Prefer string or number keys.
- **Item and spell data.** Item data may not be cached at login. Wait as Syndicator does (`C_Item.IsItemDataCachedByID` / `ContinueOnItemLoad`), or store `itemID` plus the raw link.
- **Timers.** Store every timer as remaining seconds or `time()`-based. `GetTime()` values are meaningless after a relog.
- **Localized names.** Skill names, faction names, pet family, loyalty and zone names are localized. Store IDs where an API gives them (factionID, talentID, nodeID, questID, spellID, itemID). Otherwise store the name together with `GetLocale()`.
