# cmangos-classic character import spec (code-verified)

Source: [cmangos/mangos-classic](https://github.com/cmangos/mangos-classic) at commit 5731249 (2026-10-28).
Paths below are relative to its `src/game/` unless they start with `sql/` or `shared/`.
"General knowledge" marks the few points not read from code.

---

## 0. Big-picture rules (read first)

1. **Stop mangosd before running a raw SQL script.** All GUID counters are taken from `MAX()+1` once at
   startup (`Globals/ObjectMgr.cpp:6779-6797` chars/items, `:7279-7283` pets). There is no player-name
   cache; every name/guid lookup hits the DB (`ObjectMgr.cpp:2361-2471`). Inserting while the server runs
   lets it hand out the same item/char/pet GUIDs later.
   At startup it also runs `DELETE FROM character_inventory WHERE item >= <MAX(item_instance.guid)+1>`
   (`ObjectMgr.cpp:6800-6805`), so every inventory row must have its `item_instance` row.
2. **Alternative that works with the server running: `.pdump load <file> <account> [newname] [guid]`**
   (`Tools/PlayerDump.cpp:401-720`, command `Chat/Level3.cpp:5477`, SEC_ADMINISTRATOR, console OK). It
   remaps char guid, item guids (incl. `character_inventory.bag`), pet ids, mail ids, and sets
   `at_login=1` (rename) if the name exists. Format rules are in section 14. Same row contents as below.
3. Rows are validated against the **1.12 world DB/DBCs the server loads**, not the Era client. Spell IDs,
   item IDs, quest IDs, AreaBits, taxi node IDs, faction rep indexes must exist in 1.12 data. An unknown
   spell in `character_spell` is **deleted for every character** (`Player.cpp:3122-3127`).
4. Load order inside `Player::LoadFromDB` (`Player.cpp:14064`) matters for validation:
   homebind → honor → skills (14446) → mail → spells (14455) → auras → `InitTalentForLevel` (14464) →
   default skills/spells → quests (14469) → reputation (14473) → **inventory (14475)** → actions (14489)
   → social → taxi path → cooldowns → stats → health/power (14556).
   So items are equip-checked against already-loaded skills, spells, reputation and level.

---

## 1. `characters`

### 1.1 Column list in physical (INSERT) order — `sql/base/characters.sql:640-713`

| # | column | type / default | meaning, how loaded (Player.cpp) | value to write |
|---|---|---|---|---|
| 1 | guid | int unsigned, PK | char low guid | MAX(guid)+1 |
| 2 | account | int unsigned | must equal the logged-in account id or load fails ("loading from wrong account", 14096-14102). `realmd.account.id` | account id |
| 3 | name | varchar(12) | `CheckPlayerName` must pass else `at_login|=1` and login fails (14104-14111). No unique index: check `SELECT 1 FROM characters WHERE name=?` yourself. Store normalized (first letter upper, rest lower; `normalizePlayerName`, ObjectMgr.cpp:68-86) | |
| 4 | race | tinyint | 1..8; race/class pair must exist in `playercreateinfo` (_LoadHomeBind 15547-15552, BuildEnumData 1712) | |
| 5 | class | tinyint | | |
| 6 | gender | tinyint | `& 0x01` only (14121) | 0 male, 1 female |
| 7 | level | tinyint | | |
| 8 | xp | int unsigned | XP into current level | |
| 9 | money | int unsigned | copper, clamped to `MAX_MONEY_AMOUNT = 0x7FFFFFFE` (14133-14136, Player.h:733) | |
| 10 | playerBytes | int unsigned | see 1.2 | |
| 11 | playerBytes2 | int unsigned | see 1.2 | |
| 12 | playerFlags | int unsigned | see 1.3. GM flag cleared on load keeping only hide helm/cloak (14381-14383) | 0 or 0x400/0x800 |
| 13 | position_x | float | | |
| 14 | position_y | float | | |
| 15 | position_z | float | | |
| 16 | map | int unsigned | invalid coords → relocated to homebind (14196-14204); BG map → BG entry point | |
| 17 | orientation | float | | |
| 18 | taximask | longtext | see 1.4 | 8 numbers |
| 19 | online | tinyint | | 0 |
| 20 | cinematic | tinyint | 0 → race intro cinematic plays at login (CharacterHandler.cpp:802-809) | 1 |
| 21 | totaltime | int unsigned | /played total, seconds (14373) | |
| 22 | leveltime | int unsigned | /played this level, seconds | |
| 23 | logout_time | bigint unsigned | unix time. `time_diff = now - logout_time` drives rest gain, conjured-item deletion (>15 min), sobering, instance relocation (14343-14460, 14836-14843) | `UNIX_TIMESTAMP()` |
| 24 | is_logout_resting | tinyint | only used in rest computation (14443) | 0/1 |
| 25 | rest_bonus | float | see 1.6 | |
| 26 | resettalents_cost | int unsigned | copper, last talent-reset price | 0 |
| 27 | resettalents_time | bigint unsigned | unix time of last reset | 0 |
| 28-31 | trans_x, trans_y, trans_z, trans_o | float | transport offsets | 0 |
| 32 | transguid | bigint unsigned | transport | 0 |
| 33 | extra_flags | int unsigned | GM-only flags (Player.h:479-497: 0x1 GM on, 0x2 accept tickets, 0x4 accept whispers, 0x8 taxicheat, 0x10 GM invisible, 0x20 GM chat, 0x40/0x80 auction neutral/enemy, 0x100 PvP death, 0x200 whisper restriction, 0x400 unkillable) | 0 |
| 34 | stable_slots | tinyint | purchased stable slots, clamped to `MAX_PET_STABLES = 2` (14389-14394, Pet.h:37) | 0..2 |
| 35 | at_login | int unsigned | see 1.7 | 0 |
| 36 | zone | int unsigned | AreaTable zone id; only used for char-select display. If 0, `GetZoneIdFromDB` computes it from position and writes it back (6752-6779) | zone id or 0 |
| 37 | death_expire_time | bigint unsigned | res-sickness escalation timer | 0 |
| 38 | taxi_path | text | in-flight route; `""` = not flying (Taxi.cpp:64-86) | `''` |
| 39 | honor_highest_rank | int unsigned | see 1.8 | |
| 40 | honor_standing | int unsigned | last-week standing; recomputed by `UpdateHonor` on every save | 0 |
| 41 | stored_honor_rating | float | rank points (RP); current rank derives from this | |
| 42 | stored_dishonorable_kills | int | lifetime DKs | |
| 43 | stored_honorable_kills | int | lifetime HKs (character_honor_cp kills are added on top) | |
| 44 | watchedFaction | int unsigned | `Faction.reputationListID` of the watched bar, loaded as int32; **-1 = none, write 4294967295**. Schema default 0 means "watch rep index 0" (Player.cpp:804, 14146) | 4294967295 |
| 45 | drunk | smallint unsigned | | 0 |
| 46 | health | int unsigned | clamped to max after stats (14556-14557). **0 loads an alive 0-HP char**; write a large value to get full HP | 4294967295 or max |
| 47-51 | power1..power5 | int unsigned | mana, rage(×10), focus, energy, happiness; clamped to max (14558-14562) | mana/energy large, rage 0 |
| 52 | exploredZones | longtext | see 1.5 | 64 numbers |
| 53 | equipmentCache | longtext | char-select display only, see 1.9 | 40 numbers |
| 54 | ammoId | int unsigned | item entry of equipped ammo, set unvalidated (14148) | |
| 55 | actionBars | tinyint unsigned | `PLAYER_FIELD_BYTES` byte 2 (14151) = which extra bars are shown. Bits (general knowledge, client-defined): 1 bottom-left, 2 bottom-right, 4 right, 8 right-2 | |
| 56 | grantableLevels | int unsigned | RAF, not read by LoadFromDB | 0 |
| 57 | fishingSteps | tinyint unsigned | (14564) | 0 |
| 58 | deleteInfos_Account | int, NULL | set only on soft delete (Player.cpp:4212) | NULL |
| 59 | deleteInfos_Name | varchar(12), NULL | | NULL |
| 60 | deleteDate | bigint, NULL | | NULL |

Exact LoadFromDB SELECT: `Entities/CharacterHandler.cpp:184-189`. SaveToDB: `Player.cpp:15598-15760`.

### 1.2 playerBytes / playerBytes2

```
playerBytes  = skin | face<<8 | hairStyle<<16 | hairColor<<24          (Player.cpp:806-809, 1726-1730)
playerBytes2 = facialHair | 0<<8 | bankBagSlots<<16 | restState<<24     (Player.cpp:811-812, Player.h:1088)
```
- `bankBagSlots` (byte 2) = purchased bank bag slots 0..6. Bank bags in slot 63+n need `n < bankBagSlots`,
  else `EQUIP_ERR_MUST_PURCHASE_THAT_BAG_SLOT` and the bag is mailed (Player.cpp:9625-9630).
- `restState` (byte 3): `REST_STATE_RESTED = 1`, `REST_STATE_NORMAL = 2` (Player.h:747-748). Rewritten by
  `SetRestBonus` (17026-17030): rested if rest_bonus > 10, normal if <= 1. Write 1 if rest_bonus > 10 else 2.
- Byte 1 is not set by the server; write 0.
- Appearance values are not validated on load.

### 1.3 playerFlags (`Player.h:366-388`)

0x1 group leader, 0x2 AFK, 0x4 DND, 0x8 GM (cleared on load), **0x10 ghost** (char loads dead, 14459-14461),
0x20 resting, 0x80 FFA PvP, 0x100 contested PvP, **0x200 PVP_DESIRED** (permanent PvP preference),
**0x400 hide helm, 0x800 hide cloak**, 0x1000/0x2000 play-time limits, 0x10000 sanctuary.
For an import write `hideHelm*0x400 | hideCloak*0x800 | pvp*0x200`.

### 1.4 taximask (`Player.cpp:303-315, 331-336`, `Player.h:759-779`, `Server/DBCStructure.h:1137`)

- 8 space-separated decimal uint32 (`TaxiMaskSize = 8`), trailing space allowed. Fewer tokens fill the
  front; extra ignored.
- Node id `n` (TaxiNodes.dbc ID) → word `(n-1)/32`, bit `(n-1)%32`.
- Each word is ANDed with `sTaxiNodesMask` (nodes that exist in the server's 1.12 DBC).
- Not NULL-safe in theory (`GetString` returns "" for NULL, so NULL is OK; `shared/Database/Field.h:45-48`).
- The race's starting mask (ChrRaces.startingTaxiMask) is only applied at creation (Player.cpp:294-300);
  include the home nodes yourself.

### 1.5 exploredZones (`Player.cpp:14046-14062, 14128, 6197-6213`; `Player.h:77`)

- Exactly **64** space-separated decimal uint32 (`PLAYER_EXPLORED_ZONES_SIZE = 64`). Any other count →
  whole field ignored (all unexplored). Parsed with `std::stoul` (non-numeric token = exception).
- Bit for an area: `AreaTable.exploreFlag` (DBC column 3, `m_AreaBit`, DBCStructure.h:45) →
  word `exploreFlag/32`, bit `exploreFlag%32`.
- Use the AreaBit from the **server's 1.12 AreaTable.dbc**. The Era client DB has `AreaTable.AreaBit`
  (e.g. Elwynn 126, Stormwind 688); not verified here that they equal 1.12 values.

### 1.6 rest_bonus (`Player.cpp:17010-17035, 20079-20096, 7765-7776`)

- Pool of **bonus XP points** (each point = 1 extra XP on kills). Capped at
  `PLAYER_NEXT_LEVEL_XP * 1.5 / 2` and forced to 0 at max level.
- Code comment (20084-20085): "Client automatically double the value sent". So an addon's
  `GetXPExhaustion()` is likely 2 × rest_bonus (inferred from the comment, not tested).
- On load, rest earned since `logout_time` is added (14442-14443), so set `logout_time = now`.

### 1.7 at_login (`Player.h:497-508`, handling `CharacterHandler.cpp:887-916`)

| flag | value | effect |
|---|---|---|
| AT_LOGIN_RENAME | 0x01 | char list shows rename flag; client forces rename. Use on name clash. |
| AT_LOGIN_RESET_SPELLS | 0x02 | `resetSpells()`: removes **all** spells incl. trained ranks, profession ranks, recipes; relearns only defaults + quest-reward spells (Player.cpp `resetSpells`). Don't use for imports. |
| AT_LOGIN_RESET_TALENTS | 0x04 | `resetTalents(true)`: free talent wipe at login. Useful if talent data is unreliable. |
| AT_LOGIN_FIRST | 0x20 | "first login" byte in char enum; cleared at login. |
| AT_LOGIN_RESET_TAXINODES | 0x40 | resets taximask to race default. |
| AT_LOGIN_ADD_BG_DESERTER | 0x80 | casts deserter. |

No flag forces skill/reputation recalculation.

### 1.8 Honor (`Player.cpp:14186-14191, 6453-6560`; `Tools/Formulas.h:41-102`; `Player.h:343-350, 362-364`)

- Internal rank 0..18: 0 none; 1..4 negative ranks; **5..18 = visual ranks 1..14 (Private/Scout .. Grand
  Marshal/High Warlord)**, i.e. `internal = visual + 4`.
- `honor_highest_rank` = internal rank of highest rank reached (shown on honor tab; used for "highest").
- **Current rank is recomputed from `stored_honor_rating` (RP)** on every save (`UpdateHonor` →
  `CalculateHonorRank(RP)`): RP 0 → none; 0 < RP < 2000 → visual 1; RP ≥ 2000 → visual
  `floor(RP/5000) + 2`, capped at 14 (RP > 60000 → 14). To restore visual rank R ≥ 2, write
  `RP = (R-2)*5000 + progress*5000` (R = 2 → between 2000 and 4999).
- `stored_honorable_kills` / `stored_dishonorable_kills` = lifetime totals.
- Weekly maintenance (`ObjectMgr::FlushRankPoints`, ObjectMgr.cpp:3790-3890) adds `rpEarning` only for
  chars with honor CP that week; decay modelling is server-side and approximate.

### 1.9 equipmentCache (`Player.cpp:15731-15745`; reader `BuildEnumData` 1798-1813)

40 tokens: for slot 0..18 `itemEntry enchPair`, then slot 19 (first bag) `bagEntry 0`.
`enchPair = permEnchantId | (tempEnchantId << 16)` (`MAKE_PAIR32`, shared/Common.h:105). Only the entry
is read back (for display id). Rewritten on first save.

### 1.10 Minimal valid character

All NOT NULL columns have defaults, so a row with
`guid, account, name, race, class, gender, level, position_x/y/z, map, orientation` loads. Recommended
non-defaults: `cinematic=1`, `logout_time=UNIX_TIMESTAMP()`, `watchedFaction=4294967295`,
`health=4294967295`, `power1=4294967295`, `power4=4294967295`, `playerBytes2` with restState 2,
`taximask`, `exploredZones`. A missing `character_homebind` row is auto-created from playercreateinfo
(Player.cpp:15576-15587). Default skills and spells for race/class are auto-added on every load
(`LearnDefaultSkills`, `learnDefaultSpells`, 14465-14466).

---

## 2. `character_inventory` (`sql/base/characters.sql:339-347`)

Columns (INSERT order): `guid` (char guid), `bag`, `slot` tinyint, `item` (item_instance.guid, PK),
`item_template` (item entry; **must equal item_instance.itemEntry**, used for the prototype check,
Player.cpp:14810-14823).

- `bag = 0`: slot is a player slot (below).
- `bag = <item_instance.guid of the container>`: item inside that bag; `slot` 0-based,
  `< ContainerSlots` of the bag (Player.cpp:14899-14912). Works for backpack bags and bank bags.
- Loaded `ORDER BY bag, slot` (CharacterHandler.cpp:198), so all bag=0 rows (incl. the bags themselves)
  load before bag contents, and main hand (15) before off hand (16).

Slot enums (`Entities/Player.h:558-618`, `INVENTORY_SLOT_BAG_0 = 255` at :556):

```
EQUIPMENT_SLOT_HEAD 0, NECK 1, SHOULDERS 2, BODY(shirt) 3, CHEST 4, WAIST 5, LEGS 6, FEET 7,
WRISTS 8, HANDS 9, FINGER1 10, FINGER2 11, TRINKET1 12, TRINKET2 13, BACK 14, MAINHAND 15,
OFFHAND 16, RANGED 17, TABARD 18                          EQUIPMENT_SLOT_END = 19
INVENTORY_SLOT_BAG_START 19 .. BAG_END 23                 equipped bags 19-22 (4)
INVENTORY_SLOT_ITEM_START 23 .. ITEM_END 39               backpack 23-38 (16)
BANK_SLOT_ITEM_START 39 .. BANK_SLOT_ITEM_END 63          bank 39-62 (24; header comment "28" is wrong)
BANK_SLOT_BAG_START 63 .. BANK_SLOT_BAG_END 69            bank bags 63-68 (6)
BUYBACK_SLOT_START 69 .. BUYBACK_SLOT_END 81              buyback 69-80, not loadable from DB
KEYRING_SLOT_START 81 .. KEYRING_SLOT_END 97              keyring 81-96 (16)
PLAYER_SLOT_END 118
```

- Keyring exists. Items in keyring slots `81 .. 81+GetMaxKeyringClientSize()-1` must have
  `BagFamily` keys (Player.cpp:8625-8629). Client size by level: 4 (1-39), 8 (40-49), 12 (50+)
  (Player.cpp:143-152, 11085-11092). Era keyring holds more; keep ≤ 12 keys at 60.
- Buyback slots are rejected ("prevent cheating", 8631-8636).
- What load checks (`_LoadInventory` 14787-14955):
  - unknown entry → rows deleted;
  - `Item::LoadFromDB` fails → deleted;
  - item limited to another map/zone (while alive) → deleted;
  - conjured item and `time_diff > 15 min` → deleted;
  - equip slots: `CanEquipItem(slot, …, direct_action=false)` → class/race mask, `RequiredSkill`/rank,
    `RequiredSpell`, `RequiredLevel`, weapon/armor proficiency skill present (`GetSkillValue(item skill)`),
    required reputation rank, unique-equipped, dual-wield (spell 674) for 1H in off hand, 2H blocks off hand,
    one quiver/ammo pouch (9432-9592, 9779-9840). **PvP rank requirement is not checked at load**
    (`direct_action && …RequiredHonorRank`, 9832).
  - bank bag slots: purchased-slot check + `CanUseItem`.
  - stack count vs `MaxCount`/unique (`CanTakeMoreSimilarItems`).
  - **Any item that fails placement is mailed to the char**, one item per mail (`MAX_MAIL_ITEMS = 1`,
    Mails/Mail.h:49), not deleted.
- Keep `count <= item_template.stackable`.

---

## 3. `item_instance` (`sql/base/characters.sql:1117-1133`)

INSERT order: `guid, owner_guid, itemEntry, creatorGuid, giftCreatorGuid, count, duration, charges,
flags, enchantments, randomPropertyId, durability, itemTextId`.

Parsed by `Item::LoadFromDB` (`Entities/Item.cpp:405-505`):

| column | format / meaning | notes |
|---|---|---|
| guid | item low guid, PK | MAX(guid)+1 upward |
| owner_guid | char guid | only used for deletes/mail; set it |
| itemEntry | item_template entry | same as character_inventory.item_template |
| creatorGuid | char low guid of crafter ("<Made by X>") | 0 |
| giftCreatorGuid | | 0 |
| count | stack size | |
| duration | seconds left for timed items | if `(proto.Duration==0) != (duration==0)` it is reset to proto.Duration (Item.cpp:428-434). Write proto.Duration or 0. |
| charges | **5 space-separated int32**, one per item spell slot (`MAX_ITEM_PROTO_SPELLS = 5`, ItemPrototype.h:437). Any other count → all charges stay 0 (Item.cpp:436-439) | **Must copy `item_template.spellcharges_1..5`** (e.g. `-1 0 0 0 0` for potions). Charges 0 on an item whose proto has charges → `SPELL_FAILED_NO_CHARGES_REMAIN`, item unusable (Spells/Spell.cpp:6461-6464). Negative = consumed (item destroyed at 0). |
| flags | `ITEM_DYNFLAG_*` (Item.h:187-206): 0x1 **BINDED (soulbound)**, 0x2 translated, 0x4 UNLOCKED (opened lockbox), 0x8 WRAPPED (gift), 0x200 readable | Bind flag removed if proto.Bonding == NO_BIND (Item.cpp:443-448). Server also binds BoP/quest items on store and BoE on equip during load (Player.cpp:9978-9982, 10209-10212), but write 1 for soulbound items. WRAPPED without a valid wrapper proto is cleared. |
| enchantments | **21 space-separated uint32 = 7 slots × (id, duration, charges)** (`MAX_ENCHANTMENT_SLOT = 7`, `MAX_ENCHANTMENT_OFFSET = 3`, Item.h:137-159). Exactly 21 tokens or the whole field is ignored (Object.cpp:783-799). Trailing space fine (`StrSplit` skips empties, shared/Util/Util.cpp:122-140). | see slot table |
| randomPropertyId | smallint; `ItemRandomProperties.dbc` ID (positive). Sets `ITEM_FIELD_RANDOM_PROPERTIES_ID` only | **Not expanded on load.** `SetItemRandomProperties` (Item.cpp:663-682) is only called at item creation (Item.cpp:975). You must write the property's 3 enchant ids into slots 3,4,5 yourself, or the item shows the "of the Bear" name with no stats. Classic has no suffix (negative) ids. |
| durability | current durability | clamped to proto.MaxDurability (Item.cpp:453-462); 0 = broken |
| itemTextId | `item_text.id` for written letters | 0 |

Enchantment slots (Item.h:137-148):

| slot | name | use | duration units |
|---|---|---|---|
| 0 | PERM_ENCHANTMENT_SLOT | Enchanting / armor kits / libram etc. | 0 |
| 1 | TEMP_ENCHANTMENT_SLOT | oils, stones, poisons, shaman weapon imbues | ms remaining (`duration*1000`, SpellEffects.cpp:3140); charges e.g. poison charges |
| 2 | (unnamed, `MAX_INSPECTED_ENCHANTMENT_SLOT`) | unused in classic | 0 |
| 3,4,5 | PROP_ENCHANTMENT_SLOT_0..2 | `ItemRandomProperties.enchant_id[0..2]` | 0 |
| 6 | PROP_ENCHANTMENT_SLOT_3 | cleared when main hand unequipped (Player.cpp:10268); leave 0 | 0 |

Empty string → no enchants at all (including random-property stats). Example of a "of the Bear" item:
`"<perm> 0 0 0 0 0 0 0 0 <e1> 0 0 <e2> 0 0 <e3> 0 0 0 0 0"`.

Bags: `Bag::LoadFromDB` additionally requires `ContainerSlots <= 36` (Bag.cpp:95-117).

---

## 4. Spells, talents, skills

### 4.1 `character_spell` (`characters.sql:520-527`)
INSERT order: `guid, spell, active (default 1), disabled (default 0)`. Load: `_LoadSpells`
(Player.cpp:15247-15279), `addSpell` (3117-3427).

- **Talents are stored here** as the talent's rank spell (`Talent.RankID[n]`); no separate table.
  `GetTalentSpellPos(spell)` identifies them; cost = rank+1 points (DBCStores.cpp:625-636). Talent rows are
  loaded first. Adding a rank removes lower ranks of the same talent. **Store only the highest rank per
  talent.** If total cost > `level - 9` (× Rate.Talent), all talents are reset at load
  (`UpdateFreeTalentPoints(true)`, 2771-2801, called from `InitTalentForLevel` 14464), unless the account is
  administrator.
- Non-talent ranked spells: adding rank N auto-adds lower ranks (dependent) unless a lower rank belongs to
  an unknown talent (3249-3290). Lower ranks get `active=0` when a higher rank exists (spell book
  successor logic). Write `active=1, disabled=0` for each spell; the server sorts out superseded ranks.
- **No class check.** Any spell that exists and passes `SpellMgr::IsSpellValid` (Spells/SpellMgr.cpp:2004:
  craft output item and reagents must exist, learn-spell targets valid) is kept. Invalid/unknown →
  `DELETE FROM character_spell WHERE spell = X` **for all characters** (3122-3145).
- Passive spells and talents with LEARN_SPELL effects are cast at load (3383-3393).
- Spells with `SPELL_EFFECT_SKILL` (118) set the skill tier: `UpdateSpellTrainedSkills` → `SetSkillStep`
  (5701-5710, 5497-5535). Step = EffectBasePoints+1 (SpellMgr.cpp:1625-1660). E.g. Alchemy 2259/3101/3464/11611
  = steps 1..4 (client DB: effect 118, misc 171, basepoints 0..3). If the skill row exists, value is kept
  and max becomes the tier max (75/150/225/300); if missing it is created at value 1.
- Skill-line spells flagged learn-on-get-skill (gathering tracking, etc.) are auto-learned from loaded skills
  (15270-15274, 5642-5699).
- Quest reward spells of rewarded quests are relearned (`learnQuestRewardedSpells`, 15200-15204).
- Primary profession count: `MaxPrimaryTradeSkill` (default 2) minus first-rank profession spells; not
  enforced at load (it only stops decrementing).

### 4.2 `character_skills` (`characters.sql:467-473`)
INSERT order: `guid, skill, value, max`. Load: `_LoadSkills` (Player.cpp:19463-19565).
- Unknown SkillLine → skipped (log only). `value = 0` → row deleted.
- Overrides by skill type (`GetSkillRangeType`, ObjectMgr.cpp:8640-8672):
  - languages: value = max = 300;
  - weapon skills (except fist), lockpicking, poisons: **max = level×5** (value kept);
  - armor/class/fist "mono" skills: 1/1, or level×5 if SKILL_FLAG_MAXIMIZED;
  - professions, secondary skills, riding (`SKILL_RANGE_RANK`): value and max kept as written.
- Step is derived by matching `max` to `SkillTiers.maxSkillValue` (75/150/225/300). A max that matches no
  tier gives step 0.
- **Profession skill without the rank spell**: skill is kept, and the "repair" in
  `UpdateSkillTrainedSpells` (5671-5686) re-adds rank spells with step **strictly lower** than the derived
  step, so the top rank (e.g. Artisan 11611) is not restored. Always write both the skill row and every rank
  spell.
- Limit `PLAYER_MAX_SKILLS = 127` (Player.h:76).
- Weapon skills used for equipping (`GetSkillValue(item skill)`) must have a row, or the item is mailed.

`character_forgotten_skills (guid, skill, value)`: optional, only for 1.11 weapon-skill retention.

---

## 5. `character_reputation` (`characters.sql:445-451`; `Reputation/ReputationMgr.cpp:457-523`)

INSERT order: `guid, faction, standing, flags`.

- `faction` = Faction.dbc ID with `reputationListID >= 0` (others ignored).
- **`standing` is an offset from the race/class base**: absolute rep = `BaseRepValue[idx] + standing`
  (`GetReputation`, ReputationMgr.cpp:63-72), where `idx` = first of 4 entries whose race mask and class
  mask fit (0 = any) (`GetBaseReputation` 74-85, `GetIndexFitTo` DBCStructure.h:375-385).
  So write `standing = absolute - base`. SetOneFactionReputation shows the same math (`faction.Standing = standing - BaseRep`, 316-330).
  Absolute is clamped to [-42000, 42999] (ReputationMgr.h:67-68) on changes.
  Rank thresholds of absolute rep: Hated < -6000 ≤ Hostile < -3000 ≤ Unfriendly < 0 ≤ Neutral < 3000 ≤ Friendly < 9000 ≤ Honored < 21000 ≤ Revered < 42000 ≤ Exalted (general knowledge). The Era `GetFactionInfo` barValue is already absolute.
- Flags (`ReputationMgr.h:27-36`): 0x01 VISIBLE, 0x02 AT_WAR, 0x04 HIDDEN, 0x08 INVISIBLE_FORCED,
  0x10 PEACE_FORCED, 0x20 INACTIVE, 0x40 RIVAL (TBC). Load applies only VISIBLE, INACTIVE and AT_WAR
  from the DB value through setters that respect forced flags; other bits come from
  `Faction.ReputationFlags[idx]` set in `Initialize()` (243-262). AT_WAR is forced on if rank ≤ hostile.
- Factions not in the table start at base standing with default flags.
- `characters.watchedFaction` uses `reputationListID`, not faction ID.

---

## 6. `character_queststatus` (`characters.sql:406-422`; `_LoadQuestStatus` Player.cpp:15118-15218)

INSERT order: `guid, quest, status, rewarded, explored, timer, mobcount1..4, itemcount1..4`.

`QuestStatus` (Quests/QuestDef.h:89-98): 0 NONE, 1 COMPLETE, 2 UNAVAILABLE, 3 INCOMPLETE, 4 AVAILABLE
(unused), 5 FAILED. Values ≥ 6 → NONE.

- **Completed and turned in (non-repeatable):** `status=1, rewarded=1`, rest 0 (`RewardQuest`
  Player.cpp:12675-12685 sets COMPLETE + rewarded; `GetQuestRewardStatus` 13304-13318).
- Completed repeatable: `status=0, rewarded=1`.
- **In quest log:** `rewarded=0` and status 3 (INCOMPLETE), 1 (objectives done, not turned in) or 5
  (failed). `mobcount1..4` = kill/GO/spell-cast counters (set into the log slot); `itemcount1..4` are
  **recomputed from inventory** after load (`AdjustQuestReqItemCount` 13375-13391, called 14481-14486);
  `explored=1` when the exploration/event objective is done (shows as complete).
- `timer`: only for timed quests; absolute **game time (unix seconds) of expiry**, saved as
  `remaining_ms/1000 + now` (16131). Expired → 1 ms left. 0 for others.
- Log size `MAX_QUEST_LOG_SIZE = 20` (QuestDef.h:32); extra in-log rows still load as status but don't get a
  log slot. Log order = row order returned (PK order = quest id).
- Unknown quest IDs are silently skipped (not deleted).
- `character_queststatus_weekly (guid, quest)`: exists (z2745) for `QUEST_FLAGS_WEEKLY` quests; not needed
  for Era imports.

---

## 7. `character_action` (`characters.sql:165-171`; `_LoadActions` 14633-14661; validation 5918-5983)

INSERT order: `guid, button, action, type`.
- `button` 0-based, `< MAX_ACTION_BUTTONS = 120` (Player.h:183). Era Lua action slot N → button N-1
  (general knowledge: 0-11 main page 1, 12-71 pages 2-6 / multibars, 72-119 stance/bonus bars).
- `type` (Player.h:148-155): 0x00 SPELL, 0x40 MACRO, 0x41 CMACRO, 0x80 ITEM.
- `action` < 0x01000000: spell id (must be known by the char and not passive, else dropped), item entry
  (must exist), or client macro index (unchecked; macros are client-side in 1.12).
- Invalid buttons are dropped (deleted on next save).

---

## 8. `character_homebind` (`characters.sql:269-279`; `_LoadHomeBind` 15545-15592)

INSERT order: `guid, map, zone, position_x, position_y, position_z`.
`zone` = AreaTable id of the inn's area (stored as uint16). Must be valid coords on a **non-instanceable**
map, else deleted and replaced by the race/class start position. Optional: missing row → auto-inserted.

---

## 9. Hunter pets

### 9.1 `character_pet` (`characters.sql:363-391`; `Pet::LoadPetFromDB` Pet.cpp:153-395; save 397-525)

INSERT order: `id, entry, owner, modelid, CreatedBySpell, PetType, level, exp, Reactstate, loyaltypoints,
loyalty, xpForNextLoyalty, trainpoint, name, renamed, slot, curhealth, curmana, curhappiness, savetime,
resettalents_cost, resettalents_time, abdata, teachspelldata`.

| column | meaning | value |
|---|---|---|
| id | pet number, PK; also `pet_spell.guid`. Counter = MAX(id)+1 at startup (ObjectMgr.cpp:7279-7289) | MAX(id)+1 |
| entry | creature_template entry tamed; must exist and be tameable for HUNTER_PET (204-226) | |
| owner | char guid | |
| modelid | display id (`SetDisplayId`, also char-select pet model) | creature's model |
| CreatedBySpell | spell that created it; hunter pets: **13481** (Tame Beast effect, SpellEffects.cpp:3169; client DB effect 55). Must have duration 0 or `LoadPet(permanentOnly)` skips it (Pet.cpp:206, Player::LoadPet Player.cpp:15101-15115) | 13481 |
| PetType | `SUMMON_PET 0, HUNTER_PET 1, GUARDIAN 2, MINI 3` (Pet.h:28-35) | 1 |
| level | clamped down to owner level (`SynchronizeLevelWithOwner`) | |
| exp | pet XP into level | |
| Reactstate | 0 passive, 1 defensive, 2 aggressive (AI/BaseAI/UnitAI.h:81-86) | 1 |
| loyaltypoints | int; points within current loyalty level. Level-up when > `LevelUpLoyalty[lvl-1]` = 5500, 11500, 17000, 23500, 31000, 39500 and `xpForNextLoyalty <= 0`; start values `LevelStartLoyalty` = 2000, 4500, 7000, 10000, 13500, 17500 (Pet.cpp:31-49, 766-836) | |
| loyalty | loyalty level 1..6: REBELLIOUS, UNRULY, SUBMISSIVE, DEPENDABLE, FAITHFUL, BEST_FRIEND (Pet.h:68-76) | |
| xpForNextLoyalty | owner XP still needed before next loyalty level; 0 at BEST_FRIEND. Formula: `XPForLevel(ownerLvl)*5/100` (below max level), else `XPForLevel(ownerLvl-1)*5/100` (742-757) | |
| trainpoint | int; **unspent** training points. Total = `level*(loyalty-1)` (PetHandler.cpp:699; +level per loyalty-up, +(loyalty-1) per level-up, Pet.cpp:833, 1140); minus cost of learned spells (highest `reqtrainpoints` per chain, 924-960). May be negative (overspent) | |
| name | | |
| renamed | 0 → rename allowed (UNIT_FLAG_PET_RENAME), 1 → already renamed (Pet.cpp:280) | 1 |
| slot | `PetSaveMode` (Pet.h:39-47): **0 current (active)**, 1..2 stable slots, 100 not in slot (dismissed/dead) | |
| curhealth | **must be > 0**: a hunter pet with 0 health is not loaded (`return false`, Pet.cpp:354-362). Clamped to max | large |
| curmana | focus; clamped | 100 |
| curhappiness | 0..1050000; UNHAPPY < 333000 ≤ CONTENT < 666000 ≤ HAPPY (Pet.h:145, Pet.cpp:863-870) | |
| savetime | unix time; used for aura time-diff | now |
| resettalents_cost/time | pet untrain cost/time | 0 |
| abdata | 10 pairs `"type action "` (20 tokens). Wrong count → default bar, then spells are added automatically (Unit.cpp:9752-9782, 9520-9533). | `''` |
| teachspelldata | **exactly 8 tokens** `"s1 r1 s2 r2 s3 r3 s4 r4"`. The parser walks 4 pairs without bounds checks (Pet.cpp:317-328): an empty value dereferences past the end (crash/UB). | `'0 0 0 0 0 0 0 0 '` |

Only one hunter pet may be in slot 0 or > 2 at once (save code Pet.cpp:469-474 deletes extras).
`characters.stable_slots` must be ≥ the highest stable slot used.

### 9.2 `pet_spell` (`characters.sql:1282-1287`; `Pet::_LoadSpells` 1538-1552, `Pet::addSpell`)
INSERT order: `guid (= character_pet.id), spell, active`.
`active` = `ActiveStates` (Entities/Unit.h:808-816): 0x01 PASSIVE, 0x81 (129) DISABLED (castable, autocast
off), 0xC1 (193) ENABLED (autocast on), **0 = ACT_DECIDE** (autocastable → on, else passive). Unknown spell →
deleted for all pets. Lower ranks of a chain are dropped when a higher rank is present. Family passives are
added automatically and not saved. `pet_aura`, `pet_spell_cooldown` optional.

---

## 10. `character_social` (`characters.sql:497-505`; Social/SocialMgr.h:40-45, SocialMgr.cpp:294-326)

INSERT order: `guid, friend, flags`. `flags`: 0x01 FRIEND, 0x02 IGNORED, 0x04 MUTED. `friend` is a
**character guid on this server**, so only possible for characters that exist there. Limits: 50 friends,
25 ignores (SocialMgr.h:107-108). Rows are also deleted when either side is deleted.

---

## 11. `character_honor_cp` (optional)

INSERT order: `guid, victim_type (default 4), victim, honor, date, type`. `type` 1 HONORABLE,
2 DISHONORABLE (Player.h:328-329). Holds per-kill/bonus CP for today/yesterday/this week/last week
(`UpdateHonor` 6453-6520). Not needed; lifetime totals and RP live in `characters`.

---

## 12. GUIDs and other databases

- Character guid: `MAX(characters.guid)+1` at startup (`ObjectMgr::SetHighestGuids`, ObjectMgr.cpp:6779-6786).
  Item guid: `MAX(item_instance.guid)+1` (6794-6798). Pet number: `MAX(character_pet.id)+1` (7279-7289).
  Inserting at MAX+1 with **mangosd stopped** is safe; it reads the new MAX at next start.
- Item guids are global across all item_instance rows (inventory, mail, auction), so MAX(item_instance.guid) is
  the right base.
- `realmd.realmcharacters (realmid, acctid, numchars)` (sql/base/realmd.sql:155-161): character count shown in
  the realm list and summed for the per-account limit at char creation (CharacterHandler.cpp:423). Not needed
  to log in. Refresh like the server does (World.cpp:2254-2273):
  `DELETE FROM realmcharacters WHERE acctid=A AND realmid=R; INSERT INTO realmcharacters (numchars,acctid,realmid) SELECT COUNT(*),A,R FROM characters.characters WHERE account=A;`
  (`realmID` from mangosd.conf `RealmID`).
- `realmd.account.id` = `characters.account`. Login fails if they differ.
- `character_db_version` is a schema-version guard only; nothing per character.
- `.pdump load` refuses accounts that already have ≥ 10 characters (PlayerDump.cpp:404-406).

---

## 13. Optional tables

| table | needed? | note |
|---|---|---|
| character_homebind | recommended | auto-created at race start if missing |
| character_aura | no | buffs; skip |
| character_spell_cooldown | no | |
| character_tutorial | no | keyed by **account** (`account, tut0..tut7`). No row → all tutorials show. Insert `4294967295` ×8 with `INSERT IGNORE` to hide them (Server/WorldSession.cpp:1117-1140) |
| character_stats | no | written by `_SaveStats` for web use only |
| character_account_data | no | per-char client cache |
| character_battleground_data, character_instance, character_gifts, item_loot, item_text, mail, mail_items, guild_member, group_member | no | |
| character_queststatus_weekly | no | |
| character_forgotten_skills | no | |

---

## 14. pdump file format (if using `.pdump load`)

From `Tools/PlayerDump.cpp:60-200, 401-720`:
- One row per line: ``INSERT INTO `table` VALUES ('v1', 'v2', ...);`` exactly. Table name is read from
  character 13 up to the next backtick; values are found by searching `VALUES ('` then `', '` separators
  (4 chars). **Quote every value**; an unquoted `NULL` shifts positional parsing for later columns. Max 32000
  chars per line. Escape `'` as `\'`.
- Columns in physical table order (it was `SELECT *`).
- `characters` line must come first; `character_pet` lines before `pet_spell`/`pet_aura`.
- Remapped positions: characters col 1 guid, col 2 account, col 3 name, col 35 at_login (set to 1 on clash);
  character_* col 1; character_inventory col 2 bag (non-zero only), col 4 item; item_instance col 1 guid,
  col 2 owner; character_pet col 1 id, col 3 owner; pet_* col 1. Old guids in the file can be any
  consistent numbers.
- Lines starting `IMPORTANT NOTE:` are skipped; `UPDATE character_db_version SET required_...` lines are run.
- Allowed tables only: characters, character_action, character_aura, character_homebind, character_honor_cp,
  character_inventory, character_queststatus, character_pet, character_reputation, character_skills,
  character_spell, character_spell_cooldown, mail, mail_items, pet_aura, pet_spell, pet_spell_cooldown,
  character_gifts, item_instance, item_loot, item_text (35-58). Anything else aborts the whole import.
- Updates the in-memory guid counters, so the server can stay up. Does not touch realmcharacters.

---

## 15. Gotcha checklist for the generator

1. Stop mangosd (raw SQL) or use `.pdump load`.
2. `logout_time = now`; otherwise conjured items vanish and rest is maxed.
3. `cinematic = 1`, `watchedFaction = 4294967295`, `health`/`power1`/`power4` large.
4. `item_instance.charges` = proto spell charges (5 tokens). Zero charges make potions/wands-with-charges unusable.
5. Random-property items: write the 3 enchant ids into enchant slots 3-5; `randomPropertyId` alone gives no stats.
6. `enchantments` exactly 21 tokens; `exploredZones` exactly 64; `teachspelldata` exactly 8; `abdata` 20 or empty.
7. `character_inventory.item_template == item_instance.itemEntry`; container rows `bag=0`; contents `bag=<bag item guid>`.
8. Include weapon/armor skills and proficiency spells (incl. Dual Wield 674) or gear is mailed instead of equipped.
9. Bank bags need `playerBytes2` byte 2 ≥ number of bank bag slots used.
10. Talents: highest rank only; total ≤ level-9 or they are wiped. Option: `at_login |= 0x04`.
11. Professions: skill row with max at a tier value (75/150/225/300) **and** every rank spell up to that tier.
12. Reputation `standing` is relative to Faction base for the char's race/class.
13. Honor current rank comes from `stored_honor_rating`; `honor_highest_rank` is internal (visual+4).
14. Filter every ID through the server's 1.12 DB/DBC (spells unknown to it are deleted for all chars).
15. Hunter pet: `CreatedBySpell=13481`, `PetType=1`, `slot=0` for the active pet, `curhealth>0`.
16. Name: normalized, ≤ 12 chars, valid per server name rules, unique (or set `at_login=1`).
17. Position on an open-world map (instance maps can bounce to entrance / homebind).
