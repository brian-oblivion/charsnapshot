# CharSnapshot

Back up your WoW Classic Era characters from the game client, and restore them on a 1.12 private server.

- **`CharSnapshot/`** is an Era addon. It saves a full snapshot of every character you log in to
  `WTF/Account/<account>/SavedVariables/CharSnapshot.lua`.
- **`tools/char_snapshot.py`** reads those snapshots and writes SQL for
  [vmangos](https://github.com/vmangos/core) or [cmangos-classic](https://github.com/cmangos/mangos-classic),
  both 1.12.1 server cores.

Why: if the official servers ever go away, or you just want your characters somewhere you control, the
snapshot is the only copy of your character that you hold. Keep the SavedVariables file backed up.

## Setup

Needs Python 3.11+, `curl`, `unzip` and `sqlite3`.

```sh
tools/fetch-data.sh              # reference data into data/: Era client tables + vmangos world DB (~40 MB download, ~150 MB)
tools/char_snapshot.py install   # symlinks CharSnapshot/ into the Era AddOns folder (or copy it there)
```

The tools find the Era folder (the one named `_classic_era_`) in the usual install locations. Otherwise set
`WOW_CLASSIC_ERA=/path/to/World of Warcraft/_classic_era_`.

## Workflow

1. Enable CharSnapshot in game and log in on each character. The addon scans 10 s after login, then rescans
   what changed every 15 s, outside combat. Nothing is scanned at logout: the client has emptied most data by then.
2. Some data is only readable with an NPC window open, so visit once per character: the bank, a mailbox, a
   flight master on **each** continent, and the stable master (hunters). Bind or use the hearthstone once
   so its coordinates are known. `/charsnapshot` lists what is still missing.
3. Log out or `/reload` so the client writes the SavedVariables file.
4. `tools/char_snapshot.py list` shows the characters and how old their snapshots are.
   `tools/char_snapshot.py check NAME` shows what was captured, and what won't import and why.
5. `tools/char_snapshot.py sql NAME --core vmangos --account 5 [--appearance 1,2,3,4,5]` writes
   `out/<Name>-<Realm>.vmangos.sql`. Load it into the server's characters database **with the world server
   stopped**: both cores read `MAX(guid)` once at startup. Back up the characters database first. vmangos's
   character tables are MyISAM, so a failed import can't be rolled back.

`tools/char_snapshot.py json NAME` writes the raw snapshot as JSON.

## Macros, key bindings and action bars

With the 1.12 client's Data folder (`--wow-data` or `$WOW_DATA`), the SQL also carries your macros, key bindings
and action bar layout. 1.12 keeps macros and key bindings as **account data**: the server stores them
(`account_data`, `character_account_data`, the same in vmangos and cmangos) and the client downloads them at
login, so nothing has to be copied into the client.

```sh
tools/char_snapshot.py sql NAME --core vmangos --account 5 --wow-data /path/to/WoW-1.12/Data   # with macros
tools/char_snapshot.py account-data --core vmangos --account 5 --wow-data ...   # account macros + key bindings
tools/char_snapshot.py sql NAME --ui-only --core vmangos ...   # for a character already on the server
```

`tools/ui_convert.py` does the conversion:
- **Macros** go to 1.12's format. 1.12 holds 18 account macros (Era 120): the ones on action bars go first.
  Icons are matched by name against the icons the 1.12 client has, and a question-mark icon becomes the icon of
  the macro's spell. `#showtooltip` lines are dropped (1.12 would say them in chat).
- **Era-only commands** become `/script` lines with the 1.12 API where there is one: `/use 13` →
  `UseInventoryItem(13)`, `/use Item Name` → a bag search, `/stopcasting` → `SpellStopCasting()`, `/cleartarget`,
  `/startattack`, pet commands, and `[conditionals]` on `/cast` (help, harm, dead, exists, mod, combat, pet,
  @unit) → `CastSpellByName` with `UnitIsFriend` and the like. Macros must fit 255 characters, so a mouseover
  branch that doesn't fit is dropped and target/self casting kept. What has no 1.12 equivalent
  (`/castsequence`, `/cancelaura`, `/stopmacro`, Era `C_` APIs) is left out. `check` lists every case.
- **Key bindings**: Era saves only changes from its defaults, 1.12 needs the whole table, so the changes are
  applied to 1.12's own defaults (`WTF\DefaultBindings.wtf` in the client). Commands 1.12 doesn't have are left out.
- **Action buttons** that held macros point at the converted macros, and the extra action bars that hold
  buttons are switched on.

The 1.12 client downloads changed macros after it has drawn the action bars, so macro buttons show from the
login after an import (or after `/console reloadui`).

`tools/mpq.py` reads files from the 1.12 client's MPQ archives (`tools/mpq.py Data 'WTF\DefaultBindings.wtf'`).

## What is captured

Identity, level, XP and rested XP, money, played time, guild, and honor (lifetime HK/DK, rank, progress).
Hardcore and self-found flags. Position and hearthstone point (coordinates recorded when you bind or hearth).
Equipped gear, bags, keyring and bank. Mail headers and attachments, never the body. The spellbook plus every
known skill-line spell and talent rank, talents (Talent.db2 IDs), skills, reputation, completed quests and the
quest log with progress. Flight paths, explored areas, action bars, macros, auras, cooldowns and friends. The
active pet (creature ID from its GUID, spells, loyalty, training points), the stable, and profession recipes.
Each full scan also appends a summary line to `history` (time, server time, level, money, played, item count).

Not readable by any addon:
- **appearance**: skin, face, hair, hair colour, facial hair. Only the barber chair API has it.
- which creature a **stabled** pet is.
- the mail body, because reading it can mark the mail as read.

Pass `--appearance` or fix appearance in game with a GM command.

## Import caveats (handled by the tool, reported by `check`)

- Server data is 1.12. Spells, items and quests added in Classic (Era/SoD) are left out. An unknown spell
  in `character_spell` would be deleted from every character on the server.
- Era's backpack has 20 slots and its keyring 32; 1.12 has 16, and 4/8/12 by level. The overflow goes
  into free general-purpose bag slots, otherwise into mail (one item per mail, as on 1.12).
- Random-property ("of the Bear") stats are written as enchants in slots 3-5. Item charges come from
  the item template, with remaining charges from the tooltip.
- Reputation is stored relative to the race/class base reputation, and rested XP as half the client value.
- `UnitPosition`'s z is always 0 on Era, so heights come from the nearest creature or object spawn, and the
  hearthstone height from the nearest innkeeper. The hearthstone point is checked against that innkeeper,
  which catches swapped coordinates.
- Era keeps the race riding skills (Wolf Riding, Raptor Riding...), while 1.12 mounts need Riding (762). These
  become Riding 75/150 (150 with an epic mount) plus spells 33388/33391, for both cores.
- Macros and key bindings: see above. Without the 1.12 Data folder, key bindings are left out.

## Specs

The SQL is built from code-read specs of each core's character database, with file:line citations:
[docs/vmangos.md](docs/vmangos.md) and [docs/cmangos.md](docs/cmangos.md). The addon is built against
[docs/era-api.md](docs/era-api.md), the Era Lua API as Blizzard's own UI code uses it.

## Development

```sh
tools/char_snapshot.py addon-data   # regenerate CharSnapshot/Data.lua (spell IDs checked with IsSpellKnown)
tools/char_snapshot.py lint         # every event the addon registers must exist on Era
```

`lint` needs Blizzard's UI source:
`git clone --depth 1 -b classic_era https://github.com/Gethe/wow-ui-source third_party/wow-ui-source`
(or set `WOW_UI_SOURCE`). Registering an event the client doesn't have raises an error and aborts loading
the rest of that Lua file. `tools/era_api.py NAME` looks up any Era API in that source.

## Testing status (2026-11-10)

Mock testing: the addon ran in a Lua 5.1 mock of the Era API (not included), fed with real item links,
including a simulated logout teardown. The generated SQL was checked against both cores' CREATE TABLE
statements (columns, required columns, token counts) and spot-checked against the 1.12 data.

In game, Era 1.15.9 with a level 60 character:
- **During logout the client reports empty bags, no gear, no money, no skills, no action bars and no
  completed quests.** 0.1.0 scanned at `PLAYER_LOGOUT` and overwrote good data with that. Since 0.2.0 there
  is no logout scan: events mark sections dirty, a 15 s ticker rescans them outside combat, and scans are
  refused unless the player is in the world (between `PLAYER_ENTERING_WORLD` and `PLAYER_LEAVING_WORLD`).
- `C_SpellBook.IsSpellKnown` **does** report profession recipes, so profession windows aren't required.
- Bank (main and 6 bags) and flight paths (all known nodes with their TaxiNodes IDs) are captured correctly.
- Exploration: overlay offsets and sizes are pixels on the map's art layer (`C_Map.GetMapArtLayers`), not
  0-1 positions. 0.2.2 normalises them; not yet confirmed in game.

Loaded into vmangos and logged in with a 1.12.1 client: the character arrives with gear, bags and action bars,
standing at the saved position since the height fix. The riding conversion is newer and not yet re-tested in game.
Not yet loaded into a real cmangos database.

Still to confirm in game:
- `GetStablePetInfo` index of the active pet (0 or 1).
- Whether `UnitPosition` is nil inside instances (the last outdoor position is kept).
- That field 6 of the hunter pet's GUID is the tamed creature's entry.
- That `GetCraftItemLink` for Enchanting gives an `enchant:` link with the spell ID.

## Can a snapshot prove it is genuine?

No. The player controls the client and the SavedVariables file, any signing key inside an addon is readable
Lua, and Blizzard gives addons no signed data. A hash of the GUID, realm and a nonce can be computed by anyone
who has seen the character, so it proves nothing either. What can be checked, but only while Blizzard's servers are up:
- **Ownership**: the Battle.net OAuth account profile (`profile-classic1x-<region>` namespace; the protected
  character endpoint has been reported to return 403 for Era). Or a challenge visible in the public API, such as
  "unequip X and log out".
- **Equipped gear, level, class, race and guild**: the public Profile API (equipment, character summary).
- **Freshness**: a snapshot older than the API's last login is a reverted file.

## License

MIT, see [LICENSE](LICENSE). The reference data downloaded by `tools/fetch-data.sh` comes from
[wago.tools](https://wago.tools) (Blizzard client tables) and [vmangos](https://github.com/vmangos/core) under
their own terms. It is not part of this repository.
