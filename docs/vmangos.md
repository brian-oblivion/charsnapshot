# vmangos `characters` DB: spec for inserting a restored character

Source: [vmangos/core](https://github.com/vmangos/core) at commit 9b7fb4d (2026-10-28). Paths below are relative
to its root. "P.cpp" = `src/game/Objects/Player.cpp`, "CH.cpp" = `src/game/Handlers/CharacterHandler.cpp`.
Build is `SUPPORTED_CLIENT_BUILD = CLIENT_BUILD_1_12_1` (cmake/options.cmake:22); `#if` branches below assume that.

## 0. Schema provenance and versioning

* `sql/characters.sql` already contains every `old_migrations/*_characters.sql` up to 20210607
  (e.g. skin/face/hair columns replaced playerBytes there). Only 8 later migrations touch characters:
  * 20220813085336: `character_stats` + spell_crit_chance/spell_damage/spell_healing (export only).
  * 20221001202815: new tables `account_data`, `character_account_data` (client UI cache, optional).
  * 20241002031703 + 20241114131912: `characters.instance` INT UNSIGNED DEFAULT 0, **AFTER `map`**.
  * 20241113131610 / 20250712023524: indexes only.
  * **20250428111913**: adds `characters.character_flags` INT UNSIGNED DEFAULT 0 AFTER `player_flags`, converts, then
    **DROPs `player_flags`, `at_login_flags`, `is_logout_resting`**.
  * 20260826140125: DROPs tables `worldstates`, `character_duplicate_account`.
* **The characters DB is NOT patch-versioned.** No `patch`/`build` column in any table of characters.sql.
  (World DB tables are: `item_template.patch`, `faction.build`, `taxi_nodes.build` ...; use `v_*` views / max build ≤ 5875.)
* The final positional order is confirmed by PlayerDump.cpp, which edits rows by column index:
  `changenth(line, 15, "16384") // characters.character_flags` (PlayerDump.cpp ~506), `item_instance` col 3 = owner_guid,
  col 13 = text (~535-539).
* Always emit explicit column lists in INSERTs (C++ never relies on positional order except pdump).

## 1. `characters`

### Final column list (table order)

| # | column | type / default | meaning (as read by `Player::LoadFromDB`, P.cpp:14571) |
|---|---|---|---|
| 1 | guid | int unsigned | low GUID. |
| 2 | account | int unsigned | **`realmd`/logon DB `account.id`**. Must equal the logging-in account or load fails (P.cpp ~14600: "loading from wrong account"). |
| 3 | name | varchar(12) | Must pass `ObjectMgr::CheckPlayerName` (ObjectMgr.cpp:9580: UTF-8, ≤12, ≥ MinPlayerName, no 3 identical consecutive chars, StrictPlayerNames charset) and not be a reserved name, else `CHARACTER_FLAG_RENAME` is set and login refused. Store normalized ("Xxxxx"). Not unique-indexed: check for duplicates yourself. |
| 4 | race | tinyint | 1..8 |
| 5 | class | tinyint | race/class pair must exist in `playercreateinfo` (else `_LoadHomeBind` returns false → load fails, P.cpp:16092). |
| 6 | gender | tinyint | 0/1 (`& 0x01`). |
| 7 | skin | tinyint | Separate columns (not playerBytes) → `PLAYER_BYTES` bytes (P.cpp:14657-14661). |
| 8 | face | tinyint | |
| 9 | hair_style | tinyint | |
| 10 | hair_color | tinyint | |
| 11 | facial_hair | tinyint | → `PLAYER_BYTES_2` facial style. |
| 12 | level | tinyint | |
| 13 | xp | int unsigned | XP into current level. |
| 14 | money | int unsigned | copper, clamped to `GetMaxMoney()`. |
| 15 | character_flags | int unsigned 0 | `enum CharacterFlags` (Player.h:285). See below. |
| 16 | zone | int unsigned | Only used by char-select list / player cache (written from `GetCachedZoneId()`); set the AreaTable **zone** id. |
| 17 | map | int unsigned | |
| 18 | instance | int unsigned 0 | Instance id the player logged out in. Only checked if map is a dungeon: if no matching bound instance → moved to the dungeon's exit areatrigger (P.cpp:14852-14882). Use 0 + an open-world map. |
| 19-22 | position_x, position_y, position_z, orientation | float | Must pass `MaNGOS::IsValidMapCoord` else relocated to homebind (P.cpp ~14735). No ground check. |
| 23 | transport_guid | bigint unsigned 0 | 0 |
| 24-27 | transport_x/y/z/o | float 0 | 0 |
| 28 | known_taxi_mask | longtext NULL | See taxi format below. |
| 29 | current_taxi_path | text NULL | `""`/NULL = not flying. If non-empty must be ≥2 valid node ids. |
| 30 | online | tinyint 0 | 0 |
| 31 | played_time_total | int unsigned | seconds. **Must be > 0**: 0 ⇒ intro cinematic (CH.cpp:578), "first login" in char list (P.cpp:1687), and `AddStartingItems()` if the inventory is empty (P.cpp:14993). |
| 32 | played_time_level | int unsigned | seconds at this level. |
| 33 | create_time | bigint unsigned | unix seconds; char list is `ORDER BY create_time, guid LIMIT 0,10` (CH.cpp:181). |
| 34 | logout_time | bigint unsigned | unix seconds. **Set to now (or dump time).** Load computes `timeDiff = now - logout_time` (P.cpp:14884) and uses it for offline rest gain, drunk decay, deleting conjured items (>15 min), item/aura duration decay. 0 ⇒ ~56 years offline. |
| 35 | rest_bonus | float | Rested XP in **server units = half of what the client shows** (`ComputeRest`: "Client automatically double the value sent", P.cpp:8235). So `rest_bonus = GetXPExhaustion()/2`. Max kept = `next_level_xp*1.5/2` (P.cpp:17817). Forced 0 at max level. |
| 36 | reset_talents_multiplier | int unsigned 0 | respec cost tier (0 = first respec cost). |
| 37 | reset_talents_time | bigint unsigned 0 | unix time of last respec. |
| 38 | death_expire_time | bigint unsigned 0 | res-sickness/corpse timer scaling; 0. |
| 39 | stable_slots | tinyint 0 | purchased stable slots, clamped to `MAX_PET_STABLES` = **2** (Pet.h:37, P.cpp:14908). |
| 40 | bank_bag_slots | tinyint 0 | purchased bank bag slots 0..6 (→ PLAYER_BYTES_2). Bank bag in slot 63+n needs n < bank_bag_slots (P.cpp:9876) or it fails to load (mailed). |
| 41 | extra_flags | int unsigned 0 | `PlayerExtraFlags` (Chat/AbstractPlayer.h:9): 0x1 GM_ON, 0x2 GM_ACCEPT_TICKETS, **0x4 ACCEPT_WHISPERS**, 0x8 TAXICHEAT, 0x10 GM_INVISIBLE, 0x20 GM_CHAT, 0x40/0x80 auction access, 0x100 PVP_DEATH, 0x200 WHISP_RESTRICTION, 0x400 CITY_PROTECTOR. Character creation writes 0x4 for players, 0x2 for GMs (P.cpp:~16245). |
| 42 | honor_rank_points | float 0 | RP (can be negative). Current rank is computed from it each load (HonorMgr::CalculateRank). |
| 43 | honor_highest_rank | int unsigned 0 | internal rank 0..18: 0 none, 1..4 negative, **5 = visual rank 1 (Private) … 18 = visual 14**; `visual = rank - 4` (HonorMgr.cpp CalculateRankInfo). Same numbering as the client's `UnitPVPRank`. |
| 44 | honor_standing | int unsigned 0 | last week's standing position (0 none). |
| 45 | honor_last_week_hk | int unsigned 0 | |
| 46 | honor_last_week_cp | float 0 | |
| 47 | honor_stored_hk | int 0 | lifetime HKs excluding this week's `character_honor_cp` rows (total = stored + this week's rows). |
| 48 | honor_stored_dk | int 0 | lifetime DKs, same. |
| 49 | watched_faction | int -1 | **reputation list index** (`Faction.reputationListID`), not faction id; -1 none (P.cpp:14700). |
| 50 | drunk | smallint 0 | 0 |
| 51 | health | int unsigned | clamped to max after stats are computed (P.cpp:15065). **Do not use 0** (alive with 0 HP). Writing 0x7FFFFFFF gives full health. |
| 52-56 | power1..power5 | int unsigned | index = `Powers` (SharedDefines.h:160): 1 mana, 2 rage (×10, 1000 = 100 rage), 3 focus, 4 energy, 5 happiness. Clamped to max; huge value = full. |
| 57 | explored_zones | longtext NULL | exactly 64 uint32 (see below). |
| 58 | equipment_cache | longtext NULL | char-select appearance only (see below). |
| 59 | ammo_id | int unsigned 0 | item entry of selected ammo (`PLAYER_AMMO_ID`). |
| 60 | action_bars | tinyint 0 | `PLAYER_FIELD_BYTES` action-bar toggles byte, as sent by CMSG_SET_ACTIONBAR_TOGGLES (MiscHandler.cpp:932): bit 1 bottom-left, 2 bottom-right, 4 right, 8 right-2. |
| 61 | deleted_account | int NULL | NULL for live characters. |
| 62 | deleted_name | varchar NULL | NULL |
| 63 | deleted_time | bigint NULL | NULL |
| 64 | world_phase_mask | int 0 | **Set 1** (`WORLD_DEFAULT_CHAR` = 0x01, ObjectDefines.h:146; that is what `Player::Create` writes, P.cpp:~16300). 0 is `WORLD_ALONE`. |

Removed columns (do NOT emit): `player_flags`, `at_login_flags`, `is_logout_resting` (migration 20250428111913), `cinematic` (old migration).

#### character_flags (Player.h:285-309)
0x1 INVIS_GOD, **0x2 RESTING**, 0x4 LOCKED_FOR_TRANSFER, 0x8 SILENCED, 0x10 UBERINVIS_GOD, 0x20 BEASTMASTER, 0x40 PVP_ENABLED,
0x80 PORT_AFTER_RESURRECT, 0x100 RESET_TALENTS_ON_LOGIN, 0x200 HAS_PVP_RANK, **0x400 HIDE_HELM, 0x800 HIDE_CLOAK**,
0x1000 SKINNABLE, 0x2000 GHOST, **0x4000 RENAME**, 0x8000 RENAME_NEEDS_GM_REVIEW, 0x10000 PVP_DESIRED, 0x20000 GM_MODE,
0x40000 DELETED_BY_TRANSFER, 0x80000 ON_UNSAFE_TRANSPORT, 0x100000 RENAME_FAILED, **0x200000 MOUNT_UPGRADED**.
* Login is refused if any of RENAME|LOCKED_FOR_TRANSFER|DELETED_BY_TRANSFER is set (P.cpp:14611-14616). Set 0x4000 deliberately if you want to force a rename.
* Recommend: `0x2` if logged out in an inn/city, `0x400/0x800` per helm/cloak display, `0x200000` (skips the 1.12 old-riding-skill conversion, P.cpp:15158). Optionally `0x100` to force a free talent reset on login.
* Recomputed on every save from live state (`UpdateCharacterFlags`, P.cpp:~16330).

#### known_taxi_mask (PlayerTaxi.cpp:36, PlayerTaxi.h:38)
8 (`TaxiMaskSize`, DBCStructure.h:842) space-separated **decimal uint32**, e.g. `"3456 0 0 0 0 0 0 0 "`.
Node id N (TaxiNodes id) → word `(N-1)/32`, bit `1 << ((N-1)%32)`. Bits for non-existent nodes are masked off on load.
NULL ⇒ no nodes known (character creation seeds word 0 from `ChrRaces.startingTaxiMask`).

#### explored_zones (P.cpp:14555, 14648; area bit logic P.cpp:6150-6160)
Exactly **64** (`PLAYER_EXPLORED_ZONES_SIZE`, Player.h:70) space-separated decimal uint32; any other token count ⇒ ignored entirely
(all unexplored). Area with `AreaTable.ExploreFlag` = F (vmangos world `area_template.explore_flag`) → word `F/32`, bit `1 << (F%32)`.
Trailing space is fine (StrSplit drops empty tokens).

#### equipment_cache (P.cpp:1714 read, ~16470 write)
Only used for the character-select screen. 20 pairs = 40 tokens: for slot 0..18 `itemEntry enchPair`, then slot 19 (first bag) `bagEntry 0`.
`enchPair = MAKE_PAIR32(permEnchantId, tempEnchantId)` (low 16 = perm, high 16 = temp); reader only uses the entry tokens
(`slot*2`). Rewritten on first save. NULL ⇒ naked on char select until first logout.

#### Minimal set for a clean load
guid, account, name, race, class, gender, skin, face, hair_style, hair_color, facial_hair, level, xp, money, character_flags,
zone, map, instance=0, position_x/y/z, orientation, known_taxi_mask, played_time_total>0, played_time_level, create_time,
logout_time=now, rest_bonus, bank_bag_slots, stable_slots, extra_flags=4, watched_faction=-1, health>0, power1..5,
explored_zones (64 tokens), equipment_cache (40 tokens), ammo_id, action_bars, world_phase_mask=1; everything else default/NULL.

## 2. `character_inventory` + slot enums

Columns: `guid` (owner char), `bag`, `slot`, `item_guid` (PK), `item_id`.
Load query joins `item_instance` and `ORDER BY bag, slot` (CH.cpp:91); `_LoadInventory` P.cpp:15443.

* `bag = 0` ⇒ item sits directly in a player slot (`INVENTORY_SLOT_BAG_0 = 255` is **never stored**; it is 0 in DB).
* `bag = <item_instance.guid of the container>` ⇒ item inside that bag; `slot` = **0-based** position inside the bag
  (`slot < bag->GetBagSize()`, P.cpp:15581). Saved as `container->GetGUIDLow()` (P.cpp:~16750).
* Ordering by `bag` puts all bag=0 rows (including the bags themselves) first, so containers exist before their contents.

Player slot enum (Player.h:470-547):

| range | enum | count |
|---|---|---|
| 0..18 | EQUIPMENT_SLOT_HEAD 0, NECK 1, SHOULDERS 2, BODY(shirt) 3, CHEST 4, WAIST 5, LEGS 6, FEET 7, WRISTS 8, HANDS 9, FINGER1 10, FINGER2 11, TRINKET1 12, TRINKET2 13, BACK 14, MAINHAND 15, OFFHAND 16, RANGED 17, TABARD 18 | 19 |
| 19..22 | INVENTORY_SLOT_BAG_START..END (equipped bags) | 4 |
| 23..38 | INVENTORY_SLOT_ITEM_START..END (backpack) | 16 |
| 39..62 | BANK_SLOT_ITEM_START..END (main bank) | 24 (enum comment says 28; range is 24) |
| 63..68 | BANK_SLOT_BAG_START..END (bank bags) | 6 |
| 69..80 | BUYBACK_SLOT_START..END | 12 (not stored in character_inventory; rejected by `_CanStoreItem_InSpecificSlot`) |
| 81..96 | KEYRING_SLOT_START..END | 16 |
| 118 | PLAYER_SLOT_END | |

Keyring: compiled for builds > 1.10.2 (patch 1.11 feature). Usable size by level (Player.h:987):
`<40: 4, 40-49: 8, 50-60: 12, >60: 16`; keyring slots inside that size only accept `BagFamily == KEYS` items.

Era-addon → DB mapping: `GetInventoryItemID` slot s (1..19) → slot s-1; bag-slot inventory ids 20..23 → 19..22;
bag 0 slot s (1-based) → 23+s-1; bags 1..4 → bag=<bag item guid>, slot s-1; bank (-1) slot s → 39+s-1;
bank bags 5..10 → equip slot 63..68 and contents bag=<guid>, slot s-1; keyring (-2) slot s → 81+s-1.

What happens on load (per item):
* Unknown `item_id` (not in vmangos `item_template`) ⇒ deleted from character_inventory+item_instance, logged to
  `character_deleted_items` (P.cpp:15470-15476).
* Equip slots go through `CanEquipItem(..., not_loading=false)` → `CanUseItem`: AllowableClass/Race, RequiredSkill+rank,
  RequiredSpell, **RequiredLevel**, and proficiency skill must be > 0 (P.cpp:10062-10095). Honor-rank requirement is skipped
  while loading. Bags/backpack/bank use `CanStoreItem`/`CanBankItem`.
* Any failure ⇒ row deleted and the item is **mailed** back to the character ("not equipped item" GM mail, P.cpp:15610-15650);
  not lost, but the proficiency/skill rows must exist first (skills, spells, reputation are loaded before inventory).
* Items limited to another map/zone are deleted; conjured items deleted if logged out > 15 min.
* `_ApplyAllItemMods()` then applies stats/enchants of equipped items.

## 3. `item_instance`

Final columns (table order = REPLACE order minus guid placement):
`guid, item_id, owner_guid, creator_guid, gift_creator_guid, count, duration, charges, flags, enchantments,
random_property_id, durability, text, generated_loot`. Engine InnoDB.

Save (Item.cpp:278-312) / load (`Item::LoadFromDB`, Item.cpp:383-460):

| column | format / semantics |
|---|---|
| guid | item low guid (global counter). |
| item_id | item entry. |
| owner_guid | character guid. |
| creator_guid | character low guid of crafter ("<Made by X>"), 0 if none. Only meaningful for guids on this server (use the new char's guid if self-crafted). |
| gift_creator_guid | 0 |
| count | stack size. |
| duration | remaining seconds for timed items. **Forced to proto `duration`** if `(proto.Duration==0) != (value==0)` (Item.cpp:409-413). Simplest: write `item_template.duration`. |
| charges | **5 tokens** (`MAX_ITEM_PROTO_SPELLS`, ItemPrototype.h:433) `"c1 c2 c3 c4 c5 "` (signed). Parsed with `Tokenizer(' ', 5)`; any other count ⇒ all charges stay 0. **Must be filled**: if the proto spell has `spellcharges_N != 0` and stored charge is 0, using the item fails with `SPELL_FAILED_NO_CHARGES_REMAIN` (Spell.cpp:7122-7125) and vendor price becomes 0 (ItemHandler.cpp:533). Write `item_template.spellcharges_1..5` (negative = consumed when charges run out; stackables never decrement, Spell.cpp:5029). |
| flags | `ItemDynFlags` (ItemDefines.h:199): **0x1 BOUND (soulbound)**, 0x2 TRANSLATED, 0x4 UNLOCKED (picked lockbox), 0x8 WRAPPED, 0x200 READABLE. Load strips BOUND if proto `bonding == NO_BIND` (Item.cpp:422-426). On load BoP/quest items stored in bags get bound automatically (`_StoreItem`, P.cpp:10227) and any equipped BoE/BoP/quest item gets bound (`VisualizeItem`, P.cpp:10467). Still set 0x1 for every bound item (BoE items that were equipped but now sit in bags are otherwise tradeable again). |
| enchantments | **21 tokens = 7 slots × (id duration charges)** (`MAX_ENCHANTMENT_SLOT`=7, `MAX_ENCHANTMENT_OFFSET`=3; ItemDefines.h:144-170). Parsed with `Object::_LoadIntoDataField` → `Tokenizer(' ', 21)`; **exactly 21 tokens or the whole field is ignored** (Object.cpp:1099-1112). Single spaces only (Tokenizer does not collapse doubles); trailing space OK. Slots: 0 PERM (enchanter), 1 TEMP (oils/poisons/stones; duration in **milliseconds**, Spell effect writes `damage*1000`, SpellEffects.cpp:3100; charges = poison charges), 2 unused in 1.12, **3,4,5 = random-property enchants**, 6 unused. |
| random_property_id | `ItemRandomProperties.dbc` id ("of the Bear"); smallint signed. **The server does not rebuild slots 3-5 from it on load** (`SetItemRandomProperties` is only called on item creation, Item.cpp:816). Stats come only from the enchant ids in slots 3..5, so write `ItemRandomProperties.Enchantment[0..2]` into slots 3,4,5 (duration 0, charges 0) yourself. |
| durability | current; clamped to proto `max_durability` (Item.cpp:440-447). 0 ⇒ broken (no stats). Write max if unknown. |
| text | `item_text.id` for player-written letters; 0. |
| generated_loot | 0 (lootable containers regenerate loot when opened). |

Max durability, display, stats are always taken from `item_template` (not stored).
Bags: `Bag::LoadFromDB` sets slot count from proto `container_slots` (Bag.cpp:97-116).
Example: `enchantments = '1900 0 0 0 0 0 0 0 0 1062 0 0 0 0 0 0 0 0 0 0 0 '` (Crusader + "of the Bear"-type enchant in slot 3).

## 4. Spells, talents, skills

### character_spell: `guid, spell, active (tinyint 1), disabled (tinyint 0)`
Loaded by `_LoadSpells` → `AddSpell(spell, active, learning=false, dependent=false, disabled)` (P.cpp:15810, 3459).
* **Unknown spell id, or `IsSpellValid` fails ⇒ `DELETE FROM character_spell WHERE spell = X` for ALL characters**
  (P.cpp:3462-3487). Filter against vmangos `spell_template` (1.12 ids) before inserting; never insert SoD/Era-only ids.
* Lower ranks of a non-talent spell are auto-added as dependent (not saved); superseded ranks are set inactive. Writing every
  known rank with active=1 is fine.
* `disabled=1` only for deliberately disabled spells; use 0.
* Also passive/learn effects are cast, skills granted by spells are created (`UpdateSpellTrainedSkills`, P.cpp:5770),
  and `SpellLearnSpell` dependents are added.
* After spells: `InitTalentForLevel()` and `LearnDefaultSpells()` (playercreateinfo_spell, added as dependent each login), then
  rewarded-quest spells (`LearnQuestRewardedSpells`).
* Primary professions: `InitPrimaryProfessions` sets free count = config max (2); each learned first-rank profession spell
  decrements it (P.cpp:~3650). Include the profession rank spells (Apprentice..Artisan) and all known recipes.
* Include weapon/armour proficiency spells (e.g. Plate Mail, Two-Handed Swords, Dual Wield) or equipped items fail and get mailed.

### Talents
**No separate table in vmangos.** A talent is the spell id of its current rank (`Talent.dbc RankID[n-1]`) in `character_spell`.
`AddSpell` for a talent removes other ranks of the same talent and adds `GetTalentSpellCost` to `m_usedTalentCount`
(P.cpp:3600-3612, 3666-3671). `UpdateFreeTalentPoints` (P.cpp:3217): if used > `CalculateTalentsPoints()` (level-9) the
points are only zeroed at load (no auto-reset unless `RESET_TALENTS_ON_LOGIN`). Insert only the highest rank id.

### character_skills: `guid, skill, value, max` (mediumint unsigned)
`_LoadSkills` (P.cpp:20539):
* SkillLine id must exist and have a `SkillRaceClassInfo` for the race/class, else the row is skipped (left in DB).
* Range type overrides: LANGUAGE ⇒ value=max=300; MONO ⇒ 1/1; **LEVEL (weapons, defense, ...) ⇒ max = level*5** (stored max ignored).
* `value == 0` ⇒ row deleted.
* Max 127 skills (`PLAYER_MAX_SKILLS`).
* After loading, `UpdateSkillTrainedSpells(skill, value)` learns/removes auto-learned abilities by skill value.
* Missing skill row but known spell ⇒ `UpdateSpellTrainedSkills` creates the skill (weapons at 1, or max if
  `AlwaysMaxSkillForLevel`), so write real weapon skill values to keep them.
* Professions: value/max are the real values (e.g. 300/300 Artisan). Riding (762): copy value/max from the dump
  (the riding spells' `SpellLearnSkill` entry raises it on load anyway).
* `character_forgotten_skills (guid, skill, value)`: weapon skills remembered after unlearning a talent; optional.

## 5. character_reputation: `guid, faction, standing, flags`

`ReputationMgr::LoadFromDB` (ReputationMgr.cpp:377):
* `faction` = **Faction.dbc id** (vmangos world `faction.id`); rows whose faction has `reputationListID < 0` are ignored.
* **`standing` is an offset from the base reputation**, not absolute:
  `GetReputation = GetBaseReputation(faction) + state->Standing` (ReputationMgr.cpp:82). Base = `BaseRepValue[i]` for the first i
  where `(RaceMask[i]==0 || RaceMask[i]&raceMask) && (ClassMask[i]==0 || ClassMask[i]&classMask)` (DBCStructure.h:328), else 0.
  ⇒ `standing = absolute_rep - base_rep` (absolute: Hated -42000 … Exalted 42999; client `GetFactionInfo` barValue is absolute).
  Same as save: `faction.Standing = standing - BaseRep` (ReputationMgr.cpp:258).
* `flags` (`FactionFlags`, ReputationMgr.h:31): 0x01 VISIBLE, 0x02 AT_WAR, 0x04 HIDDEN, 0x08 INVISIBLE_FORCED, 0x10 PEACE_FORCED,
  0x20 INACTIVE, 0x40 RIVAL. On load only VISIBLE, INACTIVE and AT_WAR are honoured (with forced-flag checks); others come from
  the DBC defaults set in `Initialize()`. Hostile-or-worse standing always forces AT_WAR.
* Only factions you changed need rows; the rest default to standing 0 + DBC flags.
* Loaded before inventory (item reputation requirements).

## 6. character_queststatus

Columns: `guid, quest, status, rewarded, explored, timer, mob_count1..4, item_count1..4, reward_choice` (PK guid,quest).
`QuestStatus` (QuestDef.h:91): 0 NONE, 1 COMPLETE, 2 UNAVAILABLE, 3 INCOMPLETE, 4 AVAILABLE (unused), 5 FAILED.

* **Turned-in (non-repeatable) quest:** `status=1, rewarded=1`, counters 0. (`RewardQuest`: `m_rewarded=true;
  SetQuestStatus(COMPLETE)`; repeatable ⇒ status 0, P.cpp:13168-13172.) `GetQuestRewardStatus` needs status != NONE for
  non-repeatables (P.cpp:13763). Repeatable turned in: `status=0, rewarded=1`.
* **In the quest log:** `status=3` (or 1 if all objectives done, 5 if failed), `rewarded=0`, `explored=1` if the
  exploration objective is done, `mob_count1..4` = creature/GO kill counters, `item_count1..4` (recomputed from inventory on
  load for deliver quests, `AdjustQuestReqItemCount`, P.cpp:13819). Status is not re-derived at load, so set 1 vs 3 yourself.
* Log slots are filled in **result order** (no ORDER BY; insertion order for MyISAM), max 20 (`MAX_QUEST_LOG_SIZE`).
* `timer` = absolute unix time when a timed quest expires (`m_timer/1000 + GameTime`, P.cpp:16821); 0 otherwise.
* `reward_choice` = chosen reward item id (informational).
* Quest ids not in vmangos `quest_template` are ignored (row kept).
* Rewarded quests with `RewSpellCast` re-teach that spell on load.

## 7. character_action: `guid, button, action, type`

Loaded by `MasterPlayer::LoadActions` (Chat/MasterPlayer.cpp:364) via `addActionButton` → `Player::IsActionButtonDataValid`
(P.cpp:5900) with player = nullptr:
* `button` 0..119 (`MAX_ACTION_BUTTONS` = 120, Player.h:166). Era Lua action slot n (1..120) → button n-1.
* `action` < 0x01000000.
* `type` (`ActionButtonType`, Player.h:133): **0 = spell**, 1 = "C" (click), **0x40 = 64 macro**, 0x41 = 65 click-macro,
  **0x80 = 128 item**. Spell must exist in spell store; item must exist in item_template; macro not validated
  (macro ids are client-side, 1.12 client stores macros locally, so restored macro buttons will point at nothing).
* Invalid rows are marked deleted and removed at next save.

## 8. character_homebind: `guid, map, zone, position_x, position_y, position_z`
`_LoadHomeBind` (P.cpp:16090). `zone` is actually the **area id** shown on the hearthstone. Accepted only if
`IsValidMapCoord` and the map is not instanceable; otherwise the row is deleted and replaced with `playercreateinfo`
start position. Missing row ⇒ start position inserted. One row per character (PK guid).

## 9. Hunter pet

### character_pet (table order)
`id, entry, owner_guid, display_id, created_by_spell, pet_type, level, xp, react_state, loyalty_points, loyalty,
training_points, name, renamed, slot, current_health, current_mana, current_happiness, save_time, reset_talents_cost,
reset_talents_time, action_bar_data, teach_spell_data` (Pet::SavePetToDB uses a different explicit order, Pet.cpp:540).

| column | value for a hunter pet |
|---|---|
| id | pet number (PK, global). Used as `pet_spell.guid`. |
| entry | creature_template entry; must be `IsTameable()` for HUNTER_PET (Pet.cpp:203). |
| owner_guid | character guid |
| display_id | creature display id (also shown on char select for slot 0). |
| created_by_spell | **13481** (Tame Beast effect spell, `EffectTameCreature` stores `m_spellInfo->Id`, SpellEffects.cpp:3129). A spell with duration > 0 would make it a temporary summon. |
| pet_type | `PetType` (Pet.h:28): 0 SUMMON, **1 HUNTER**, 2 GUARDIAN, 3 MINI. |
| level, xp | xp = `UNIT_FIELD_PETEXPERIENCE`. Pet level > owner level is synced down. |
| react_state | 0 passive, 1 defensive, 2 aggressive. |
| loyalty_points | points within the loyalty level (level-up at 5500/11500/17000/23500/31000…, start 2000/4500/7000/10000/13500…, Pet.cpp:39-56). |
| loyalty | `LoyaltyLevel` 1 Rebellious … 6 Best Friend (Pet.h:57). |
| training_points | **available TP balance** (`m_trainingPoints`; gains `level` per loyalty-level-up and `loyalty-1` per pet level, spending subtracts). = total − spent from `GetPetTrainingPoints()`. Display is derived (`GetDispTP`, Pet.cpp:989). |
| name | varchar(100) |
| renamed | 1 = already renamed (rename disabled), 0 = can still rename (UNIT_FLAG_PET_RENAME). |
| slot | `PetSaveMode` (Pet.h:40): **0 = current/active**, **1..2 = stable slots** (≤ characters.stable_slots), **100 = not in slot** (dismissed/unsummoned, still "Call Pet"-able). Only one hunter pet may be in {0, >2}. |
| current_health | **>0** (0 ⇒ pet loads dead, Pet.cpp:419). Big value clamps to max. |
| current_mana | focus is not stored; 0. |
| current_happiness | 0..1,050,000 (`HAPPINESS_LEVEL_SIZE` 333000: <333000 unhappy, ≥666000 happy). |
| save_time | unix time (aura decay). |
| reset_talents_cost/time | 0 |
| action_bar_data | **20 tokens = 10 × "type action"** (`LoadPetActionBar`, Unit.cpp:8528; wrong count ⇒ default bar). Types (`ActiveStates`, UnitDefines.h:670): 0x07 COMMAND, 0x06 REACTION, 0x81 spell castable, 0xC1 spell autocast, 0x01 passive. Default: `"7 2 7 1 7 0 129 0 129 0 129 0 129 0 6 2 6 1 6 0"` (attack/follow/stay, 4 spell slots, aggressive/defensive/passive). Spell entries not in spell store are blanked. |
| teach_spell_data | 8 tokens = 4 × "petAbility teachSpell" (Beast Training learnable pairs); `"0 0 0 0 0 0 0 0 "`. |

### pet_spell: `guid (= character_pet.id), spell, active`
`active` = ActiveStates: 0xC1 (193) autocast on, 0x81 (129) castable/autocast off, 0x01 passive, **0 = let server decide**
(passive → 0x01, else 0x81, Pet.cpp:1934-1940). Insert only the highest rank (a lower rank after a higher one is ignored).
Family passives are added automatically (`LearnPetPassives`) and not saved. Unknown spell ⇒ deleted for all pets.
`pet_aura`, `pet_spell_cooldown`: optional.

**Pets are read from an in-memory cache built at server start** (`sCharacterDatabaseCache.LoadAll()`, World.cpp:1846;
`LoadPetFromDB` reads `m_pTmpCache`, Pet.cpp:120-160). Rows inserted while the server runs are invisible until restart
(or `.reload`-style `LoadAll(petId)`, ServerCommands.cpp:1723).

## 10. character_social, character_honor_cp

* `character_social (guid, friend, flags)`: friend = another character guid **on this server**; flags 0x1 FRIEND, 0x2 IGNORED,
  0x4 MUTED (SocialMgr.h:45). Limits 50 friends / 25 ignores. Not needed; only meaningful if the friends exist locally.
* `character_honor_cp (guid, victim_type, victim_id, cp, date, type)`: this week's individual kills (deleted at weekly
  maintenance). Not needed; put lifetime totals in `honor_stored_hk/dk`.
* Other optional per-char tables: `character_aura`, `character_spell_cooldown`, `character_instance`,
  `character_battleground_data`, `character_account_data`, `character_gifts`, `item_loot`, `item_text`, `mail`, `mail_items`,
  `character_stats` (export only, written on save).

## 11. GUID allocation, caches, logon DB

* At startup `ObjectMgr::SetHighestGuids` (ObjectMgr.cpp:7942): `m_CharGuids.LoadFromDB("guid","characters")`,
  `m_ItemGuids.LoadFromDB("guid","item_instance")` = `SELECT MAX(...)`, plus an optional **gap-reuse pool**
  (`ReusableGuidPoolSize` config) filled from holes below MAX (ObjectGuid.cpp:115-160).
* It then deletes `character_inventory`, `auction`, `mail_items`, `item_loot`, `character_gifts`, `petition` rows whose
  item guid ≥ next free item guid (ObjectMgr.cpp:7951-7956): every `character_inventory.item_guid` must have its
  `item_instance` row.
* Pet numbers: `GeneratePetNumber` asks the pet cache for the next free number (ObjectMgr.cpp:8572).
* Name/guid/account/position cache: `LoadPlayerCacheData()` at startup (World.cpp:1638, ObjectMgr.cpp:688) and
  `LoadPlayerPhaseFromDb()` (World.cpp:1843).
* ⇒ **Insert with mangosd stopped**, using `MAX(characters.guid)+1`, `MAX(item_instance.guid)+1…`, `MAX(character_pet.id)+1`.
  This is safe because the generators and caches are rebuilt from the DB at next start. Inserting while running risks guid
  collisions (generators hand out MAX+1 and pooled gaps) and the char/pet would be missing from the caches.
  Even the built-in `.pdump load` (PlayerDump.cpp:412) does not refresh the player or pet caches.
* Include soft-deleted characters (`deleted_time IS NOT NULL`, account set to 0) in MAX; they keep their guid.
* `characters.account` → logon DB (`realmd`) **`account.id`** (sql/logon.sql:14). The account must exist to log in.
* `realmcharacters (realmid, acctid, numchars)` in the logon DB is cosmetic (realm list count); refreshed on char enum
  (CH.cpp:314) and by AccountMgr. Not required.
* Char select shows max 10 characters per account (`LIMIT 0,10`).

## 12. Other load-time gotchas

* World DB must have every referenced id at patch 1.12 (`v_item_template`, `spell_template`, `quest_template`, `faction`,
  `area_template`, `taxi_nodes`, `creature_template`). Era-only/SoD ids fail as described above.
* Transport / BG / dungeon positions get relocated; use an outdoor continent position (map 0 or 1).
* Ghost/dead state comes from `CHARACTER_FLAG_GHOST` + auras; leave unset.
* Riding: patch 1.12 world (`sWorld.GetWowPatch() >= WOW_PATCH_112`) converts old per-race riding skills unless skill 762
  is present or flag 0x200000 is set (P.cpp:15158, 15166-15220). Give skill 762 + spell 33388 (Apprentice) / 33391 (Journeyman).
* `GetMaxMoney()` clamps gold.
* Spells/skills/talents, reputation and quest rows are all loaded **before** inventory, so equipment checks see them.
* Auto-save rewrites `characters` with REPLACE on every save; anything you put in dropped/unknown columns is lost.

## 13. Differences from cmangos-classic (`sql/base/characters.sql` there), in passing

* cmangos `characters` still packs appearance into `playerBytes`/`playerBytes2`, keeps `playerFlags`, `at_login`,
  `cinematic`, `is_logout_resting`, uses camelCase names (`taximask`, `totaltime`, `leveltime`, `exploredZones`,
  `equipmentCache`, `ammoId`, `actionBars`, `watchedFaction`, `deleteInfos_*`), and has `grantableLevels`, `fishingSteps`;
  honor columns are `stored_honor_rating`, `stored_honorable_kills`, `stored_dishonorable_kills` (no rank points/standing split
  like vmangos). No `character_flags`, `instance`, `world_phase_mask`, `create_time`.
* cmangos `item_instance` columns: `itemEntry, creatorGuid, giftCreatorGuid, ..., randomPropertyId, itemTextId` (no
  `generated_loot`); `character_inventory` uses `item`/`item_template`.
* cmangos `character_pet`: `owner, modelid, CreatedBySpell, PetType, exp, Reactstate, loyaltypoints, xpForNextLoyalty,
  trainpoint, curhealth, curmana, curhappiness, savetime, abdata, teachspelldata`.
* A generator must therefore target one core; the two schemas are not interchangeable.
