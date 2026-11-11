#!/usr/bin/env python3
"""Character snapshots from the CharSnapshot addon (CharSnapshot/): status, JSON and SQL for private servers.

  tools/char_snapshot.py list                       # characters in the SavedVariables, with snapshot age
  tools/char_snapshot.py check NAME                 # what's captured, what's missing, what won't import
  tools/char_snapshot.py json NAME                  # out/<Name>-<Realm>.json (raw snapshot)
  tools/char_snapshot.py sql NAME --core vmangos --account 5 [--name NewName] [--appearance 1,2,3,4,5] [-o out.sql]
  tools/char_snapshot.py sql NAME --ui-only ...     # only macros, macro buttons, bindings, for a character already there
  tools/char_snapshot.py account-data --core vmangos --account 5 [--replace]   # account macros + key bindings
  tools/char_snapshot.py addon-data                 # regenerate CharSnapshot/Data.lua (candidate spell IDs)
  tools/char_snapshot.py lint                       # check the addon's event names against the Era API docs
  tools/char_snapshot.py install                    # symlink the addon into the Era AddOns folder

NAME is a character name, optionally Name-Realm. The SavedVariables are read from
<Era folder>/WTF/Account/*/SavedVariables/CharSnapshot.lua (parsed as data, never executed). The Era folder
(the one named _classic_era_) is $WOW_CLASSIC_ERA, or found in the usual install locations.
Reference data comes from tools/fetch-data.sh (into data/).

Macros and key bindings (tools/ui_convert.py) go into the server's account data, which the 1.12 client downloads
at login. Key bindings, and checking macro icons, need the 1.12 client's Data folder: --wow-data or $WOW_DATA.

SQL targets (specs in docs/): cmangos-classic and vmangos, both 1.12 cores. IDs are checked
against the 1.12 world data in data/vmangos.sqlite: spells, items and quests the server doesn't know are
left out (an unknown spell in character_spell gets deleted from every character on the server). The same
1.12 data is used for cmangos; the two cores' world DBs are close but not identical.
Run the SQL with the world server STOPPED: both cores take MAX(guid) once at startup.

Not restorable from the client: appearance (skin, face, hair, hair colour, facial hair) has no API outside
the barber chair. Pass --appearance or fix it in game with a GM command.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import lua_data
import ui_convert
from lua_data import entries, get

ROOT = Path(__file__).resolve().parent.parent
CLIENT_DB = ROOT / "data" / "era-client.sqlite"
WORLD_DB = ROOT / "data" / "vmangos.sqlite"
OUT = ROOT / "out"
ADDON = ROOT / "CharSnapshot"
ERA_CANDIDATES = [
    "C:/Program Files (x86)/World of Warcraft/_classic_era_",
    "C:/Program Files/World of Warcraft/_classic_era_",
    "/Applications/World of Warcraft/_classic_era_",
    "~/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_classic_era_",
    "~/Games/battle-net/drive_c/Program Files (x86)/World of Warcraft/_classic_era_",
    "~/.wine/drive_c/Program Files (x86)/World of Warcraft/_classic_era_",
]


def era_dir():
    """The Classic Era install folder (_classic_era_): $WOW_CLASSIC_ERA, else the first usual location found."""
    if os.environ.get("WOW_CLASSIC_ERA"):
        path = Path(os.environ["WOW_CLASSIC_ERA"]).expanduser()
        if not path.is_dir():
            raise SystemExit(f"WOW_CLASSIC_ERA={path} is not a folder")
        return path
    for c in ERA_CANDIDATES:
        path = Path(c).expanduser()
        if path.is_dir():
            return path
    raise SystemExit("Classic Era folder not found: set WOW_CLASSIC_ERA to the folder named _classic_era_")

STALE_DAYS = 14
HUNTER_TAME_SPELL = 13481
LOYALTY_START = [0, 2000, 4500, 7000, 10000, 13500, 17500]  # points at the start of loyalty level 1..6
HAPPINESS = {1: 166500, 2: 499500, 3: 832500}  # middle of unhappy / content / happy (0..1050000)
VMANGOS_PET_BAR = "7 2 7 1 7 0 129 0 129 0 129 0 129 0 6 2 6 1 6 0"
KEYRING_SIZE = [(40, 4), (50, 8), (999, 12)]  # usable keyring slots by level on 1.12 servers
RIDING = 762
RACE_RIDING = {148, 149, 150, 152, 533, 553, 554, 713}  # horse, wolf, tiger, ram, raptor, mechanostrider, undead horse, kodo


# ---------------------------------------------------------------------------------------------- loading

def saved_variables():
    return sorted((era_dir() / "WTF" / "Account").glob("*/SavedVariables/CharSnapshot.lua"))


def characters():
    """[(name, realm, snapshot, account_db)] across every account's SavedVariables."""
    out = []
    for path in saved_variables():
        db = lua_data.load(path).get("CharSnapshotDB") or {}
        db["_account_dir"] = path.parent.parent  # WTF/Account/<account>: Era's bindings and macro files
        for realm, chars in entries(db.get("chars")):
            for name, snap in entries(chars):
                out.append((name, realm, snap, db))
    return out


def find(query):
    name, _, realm = query.partition("-")
    hits = [c for c in characters() if c[0].lower() == name.lower() and (not realm or c[1].lower() == realm.lower())]
    if not hits:
        raise SystemExit(f"{query}: not found in {', '.join(map(str, saved_variables())) or 'any CharSnapshot.lua'}")
    if len(hits) > 1:
        raise SystemExit(f"{query}: several realms ({', '.join(c[1] for c in hits)}); use Name-Realm")
    return hits[0]


def age(t):
    if not t:
        return "never"
    d = time.time() - t
    return f"{d / 3600:.1f} h ago" if d < 86400 else f"{d / 86400:.1f} days ago"


def item_id(item_string):
    return int(item_string.split(":")[1])


def parse_item(item_string):
    """item:itemID:enchantID:gem1:gem2:gem3:gem4:suffixID:uniqueID:linkLevel:... -> (id, enchant, suffix)."""
    f = item_string.split(":")
    num = lambda i: int(f[i]) if len(f) > i and f[i].lstrip("-").isdigit() else 0
    return num(1), num(2), num(7)


# ---------------------------------------------------------------------------------------------- macros, bindings

class UI:
    """Converts a character's macros and key bindings (tools/ui_convert.py). client is None without --wow-data."""

    def __init__(self, wow_data):
        try:
            self.era = ui_convert.EraData(CLIENT_DB)
        except sqlite3.OperationalError as e:
            raise SystemExit(f"{CLIENT_DB}: {e}; run tools/fetch-data.sh era")
        path = wow_data or os.environ.get("WOW_DATA")
        self.client = ui_convert.Client112(Path(path).expanduser()) if path else None

    def era_bindings(self, db, realm=None, name=None):
        """Era's bindings-cache.wtf for the account, or for one character (character-specific bindings)."""
        base = db.get("_account_dir")
        path = (base / realm / name if realm else base) / "bindings-cache.wtf" if base else None
        return path.read_text(encoding="utf-8", errors="replace") if path and path.exists() else None


# ---------------------------------------------------------------------------------------------- reference data

class Ref:
    """1.12 world data (vmangos) plus Era client tables for talents, skills and random properties."""

    def __init__(self):
        for db in (CLIENT_DB, WORLD_DB):
            if not db.exists():
                raise SystemExit(f"{db} missing: run tools/fetch-data.sh")
        w = sqlite3.connect(WORLD_DB)
        c = sqlite3.connect(CLIENT_DB)
        self.items = {r[0]: r for r in w.execute(
            "SELECT entry, name, stackable, spellcharges_1, spellcharges_2, spellcharges_3, spellcharges_4, "
            "spellcharges_5, max_durability, duration, bag_family, container_slots, bonding FROM v_item_template")}
        self.spells = {r[0] for r in w.execute("SELECT entry FROM v_spell_template")}
        self.mount_rank = {r[0]: r[1] for r in w.execute(
            "SELECT entry, required_skill_rank FROM v_item_template WHERE required_skill = 762")}
        self.quests = {r[0]: r for r in w.execute(
            "SELECT entry, SpecialFlags, ReqCreatureOrGOId1, ReqCreatureOrGOId2, ReqCreatureOrGOId3, "
            "ReqCreatureOrGOId4 FROM v_quest_template")}
        self.factions = {r[0]: r for r in w.execute(
            "SELECT id, reputation_list_id, base_rep_race_mask1, base_rep_race_mask2, base_rep_race_mask3, "
            "base_rep_race_mask4, base_rep_class_mask1, base_rep_class_mask2, base_rep_class_mask3, "
            "base_rep_class_mask4, base_rep_value1, base_rep_value2, base_rep_value3, base_rep_value4 FROM v_faction")}
        taxi = w.execute("SELECT id, name FROM v_taxi_nodes").fetchall()
        self.taxi_by_name = {name: nid for nid, name in taxi}  # fallback only: some names repeat (Cenarion Hold)
        self.taxi_ids = {nid for nid, _ in taxi}
        self.areas = {r[0]: r for r in w.execute("SELECT entry, map_id, zone_id, explore_flag, name FROM area_template")}
        self.creatures = {r[0]: r for r in w.execute(
            "SELECT entry, name, display_id1, type, pet_family FROM v_creature_template")}
        self.start = {(r[0], r[1]): r[2:] for r in w.execute(
            "SELECT race, class, map, zone, position_x, position_y, position_z FROM playercreateinfo")}
        self.innkeepers = w.execute(
            "SELECT c.map, c.position_x, c.position_y, c.position_z FROM v_creature c "
            "JOIN v_creature_template t ON t.entry = c.id WHERE t.subname = 'Innkeeper'").fetchall()
        # Spawn positions, for heights: the client's UnitPosition always returns z = 0 on Era.
        self.spawns = {}
        for m, x, y, z in w.execute("SELECT map, position_x, position_y, position_z FROM v_creature WHERE map IN (0, 1) "
                                    "UNION ALL SELECT map, position_x, position_y, position_z FROM v_gameobject "
                                    "WHERE map IN (0, 1)"):
            self.spawns.setdefault((m, int(x // 50), int(y // 50)), []).append((x, y, z))
        self.talents = {r[0]: [s for s in r[1:] if s] for r in c.execute(
            "SELECT ID, SpellRank_0, SpellRank_1, SpellRank_2, SpellRank_3, SpellRank_4, SpellRank_5, SpellRank_6, "
            "SpellRank_7, SpellRank_8 FROM Talent")}
        self.talent_spells = {s for ranks in self.talents.values() for s in ranks}
        self.skills = {}
        for sid, name in c.execute("SELECT ID, DisplayName_lang FROM SkillLine ORDER BY ID"):
            self.skills.setdefault(name, []).append(sid)
        self.random_props = {r[0]: [e for e in r[1:4]] for r in c.execute(
            "SELECT ID, Enchantment_0, Enchantment_1, Enchantment_2 FROM ItemRandomProperties")}

    def ground_z(self, map_id, x, y, max_dist=40):
        """(z, distance) of the nearest creature or object spawn within max_dist yards, or None."""
        best = None
        cx, cy = int(x // 50), int(y // 50)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for sx, sy, sz in self.spawns.get((map_id, cx + dx, cy + dy), ()):
                    d = ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5
                    if d <= max_dist and (best is None or d < best[1]):
                        best = (sz, d)
        return best

    def area(self, name, map_id, zone_only=False):
        for a in self.areas.values():
            if a[4] == name and a[1] == map_id and (not zone_only or a[2] == 0):
                return a[0]
        return 0


# ---------------------------------------------------------------------------------------------- building

class Build:
    """Core-independent import plan for one character. self.warnings explains everything left out."""

    def __init__(self, snap, ref, name=None, appearance=None, db=None, ui=None, realm=None):
        self.s, self.ref, self.warnings = snap, ref, []
        self.db, self.ui, self.realm = db or {}, ui, realm
        p = snap.get("player") or {}
        if not p:
            raise SystemExit("snapshot has no player section: log in once with the addon enabled")
        self.player = p
        self.name = (name or p["name"])[:1].upper() + (name or p["name"])[1:].lower()
        self.race, self.cls = p["raceID"], p["classID"]
        self.race_bit, self.class_bit = 1 << (self.race - 1), 1 << (self.cls - 1)
        self.level = p["level"]
        self.gender = 0 if p.get("sex") == 2 else 1
        if appearance:
            self.appearance = appearance
        else:
            self.appearance = [0, 0, 0, 0, 0]
            self.warn("appearance is unknown to addons; using skin/face/hair 0 (pass --appearance or fix in game)")
        self.now = int(time.time())
        self.items = []  # dicts: key, parent (key or None), slot, entry, count, flags, enchants, prop, dur, charges
        self.mails = []
        self.build_position()
        self.build_spells()
        self.build_skills()
        self.build_reputation()
        self.build_quests()
        self.build_inventory()
        self.build_mail()
        self.build_actions()
        self.build_taxi()
        self.build_explored()
        self.build_pet()
        self.build_honor()
        self.build_riding()
        self.build_ui()

    def warn(self, msg):
        self.warnings.append(msg)

    # -- position and hearth
    def build_position(self):
        pos, hearth = self.s.get("position") or {}, self.s.get("hearth") or {}
        bind = self.player.get("bind")
        self.homebind = None
        if hearth.get("x") is not None and hearth.get("name") == bind and hearth.get("map") in (0, 1):
            area = self.ref.area(bind, hearth["map"]) or self.ref.area(hearth.get("subzone"), hearth["map"])
            # A bind point is next to an innkeeper: that checks the coordinates (and their axis order), and the
            # innkeeper's floor gives the height, which the client doesn't report (z is always 0).
            dist, inn_z = min(((((x - hearth["x"]) ** 2 + (y - hearth["y"]) ** 2) ** 0.5, z)
                               for m, x, y, z in self.ref.innkeepers if m == hearth["map"]), default=(1e9, 0))
            z = hearth["z"] or inn_z
            if dist > 80:
                self.warn(f"hearthstone point is {dist:.0f} yards from the nearest innkeeper; check its coordinates")
                z = hearth["z"] or (self.ref.ground_z(hearth["map"], hearth["x"], hearth["y"]) or (0,))[0]
            self.homebind = (hearth["map"], area, hearth["x"], hearth["y"], z + 0.5 if not hearth["z"] else z)
        else:
            self.warn(f"hearthstone location ({bind}) has no coordinates: bind or hearth once with the addon on. "
                      "The server will use the race start location")
        start = self.ref.start.get((self.race, self.cls))
        ground = None
        if pos.get("x") is not None and pos.get("map") in (0, 1):
            ground = (pos["z"], 0) if pos.get("z") else self.ref.ground_z(pos["map"], pos["x"], pos["y"])
        if ground:
            # The client reports z = 0, so the height comes from the nearest spawn; +0.5 so the character lands
            # on the floor instead of in it.
            z = ground[0] if pos.get("z") else ground[0] + 0.5
            self.pos = (pos["map"], pos["x"], pos["y"], z, pos.get("facing") or 0)
            self.zone = self.ref.area(pos.get("zone"), pos["map"], zone_only=True)
        elif pos.get("x") is not None and self.homebind:
            self.warn("no spawn near the last position to take the height from; placing the character at the hearthstone")
            self.pos = (*[self.homebind[i] for i in (0, 2, 3, 4)], 0)
            self.zone = 0
        elif self.homebind:
            self.warn("last position is not in the open world; placing the character at the hearthstone")
            self.pos = (*[self.homebind[i] for i in (0, 2, 3, 4)], 0)
            self.zone = 0
        else:
            self.warn("no usable position; placing the character at the race start location")
            self.pos = (start[0], start[2], start[3], start[4], 0)
            self.zone = start[1]

    # -- spells and talents
    def build_spells(self):
        spells = self.s.get("spells") or {}
        known = set(spells.get("known") or []) | set(spells.get("book") or [])
        # Recipes from profession windows. Beast Training (hunters) is also a "craft" window, but its entries
        # are pet abilities, not character spells, so only windows of skills the character has are used.
        skill_names = {s.get("name") for s in (self.s.get("skills") or {}).get("list") or []}
        for prof, data in entries(self.s.get("professions")):
            if prof in skill_names:
                known |= {int(k) for k, _ in entries(data.get("recipes"))}
        talents = (self.s.get("talents") or {}).get("talents") or []
        if not talents:
            self.warn("no talent data")
        known -= self.ref.talent_spells  # the talent's highest rank is added below, not every rank
        self.talent_spells = []
        for t in talents:
            ranks = self.ref.talents.get(t["id"])
            if not ranks or t["rank"] > len(ranks):
                self.warn(f"talent {t.get('name')} ({t['id']}) not in the client Talent table")
                continue
            self.talent_spells.append(ranks[t["rank"] - 1])
        known |= set(self.talent_spells)
        unknown = sorted(s for s in known if s not in self.ref.spells)
        if unknown:
            self.warn(f"{len(unknown)} spells don't exist on a 1.12 server and are left out "
                      f"(Era/SoD additions): {unknown[:12]}{' ...' if len(unknown) > 12 else ''}")
        self.spells = sorted(s for s in known if s in self.ref.spells)
        if not spells:
            self.warn("no spell data")

    def build_skills(self):
        self.skills = []
        sk = self.s.get("skills") or {}
        if sk.get("locale") and sk["locale"] not in ("enUS", "enGB"):
            self.warn(f"skills were saved in {sk['locale']}; mapping uses English names and may miss some")
        for s in sk.get("list") or []:
            if not s.get("name"):
                continue  # empty entries from a scan while the client was logging out (addon < 0.2)
            ids = self.ref.skills.get(s["name"])
            if not ids:
                self.warn(f"skill {s['name']!r} not found in SkillLine")
                continue
            if len(ids) > 1:
                continue  # talent-tree names (Holy, Survival...): class skills the server adds itself
            self.skills.append((ids[0], s["rank"], s["max"]))

    # -- reputation
    def base_rep(self, f):
        for i in range(4):
            race_mask, class_mask, value = f[2 + i], f[6 + i], f[10 + i]
            if (not race_mask or race_mask & self.race_bit) and (not class_mask or class_mask & self.class_bit):
                return value
        return 0

    def build_reputation(self):
        self.reps, self.watched = [], None
        for r in self.s.get("reputation") or []:
            f = self.ref.factions.get(r.get("id"))
            if not f or f[1] is None or f[1] < 0:
                continue
            flags = 0x1 | (0x2 if r.get("atWar") else 0) | (0x20 if r.get("inactive") else 0)
            self.reps.append((r["id"], r["value"] - self.base_rep(f), flags))
            if r.get("watched"):
                self.watched = f[1]

    # -- quests
    def build_quests(self):
        q = self.s.get("quests") or {}
        self.quests, missing = [], 0
        for qid in q.get("completed") or []:
            row = self.ref.quests.get(qid)
            if not row:
                missing += 1
                continue
            repeatable = (row[1] or 0) & 1
            self.quests.append(dict(id=qid, status=0 if repeatable else 1, rewarded=1))
        if missing:
            self.warn(f"{missing} completed quests don't exist on a 1.12 server (Era/SoD-only) and are left out")
        for entry in (q.get("log") or [])[:20]:
            row = self.ref.quests.get(entry["id"])
            if not row:
                self.warn(f"quest log: {entry.get('title')} ({entry['id']}) not on a 1.12 server")
                continue
            status = {1: 1, -1: 5}.get(entry.get("complete"), 3)
            mobs, explored = [0, 0, 0, 0], 0
            slots = [i for i in range(4) if row[2 + i]]
            kills = [o for o in entry.get("objectives") or [] if o.get("type") in ("monster", "object")]
            for slot, o in zip(slots, kills):
                mobs[slot] = o.get("have") or (o.get("need") if o.get("done") else 0) or 0
            if any(o.get("type") == "event" and o.get("done") for o in entry.get("objectives") or []):
                explored = 1
            timer = self.now + int(entry["timeLeft"]) if entry.get("timeLeft") else 0
            self.quests.append(dict(id=entry["id"], status=status, rewarded=0, explored=explored, timer=timer, mobs=mobs))

    # -- items
    def add_item(self, item, parent=None, slot=0, where=""):
        entry, enchant, suffix = parse_item(item["s"])
        proto = self.ref.items.get(entry)
        if not proto:
            self.warn(f"{where}: item {item.get('n')} ({entry}) doesn't exist on a 1.12 server, left out")
            return None
        stackable = proto[2] or 1
        count = min(item.get("c") or 1, stackable)
        charges = list(proto[3:8])
        if item.get("ch") is not None:  # remaining charges from the tooltip, on the first spell with charges
            for i, c in enumerate(charges):
                if c:
                    charges[i] = -item["ch"] if c < 0 else item["ch"]
                    break
        enchants = [[0, 0, 0] for _ in range(7)]
        enchants[0][0] = enchant
        if suffix > 0:
            props = self.ref.random_props.get(suffix)
            if props:
                for i, e in enumerate(props):
                    enchants[3 + i][0] = e or 0
            else:
                self.warn(f"{where}: {item.get('n')}: random property {suffix} unknown")
        max_dur = proto[8] or 0
        dur = item.get("d")
        durability = min(get(dur, 1, max_dur), max_dur) if dur else max_dur
        rec = dict(key=len(self.items) + 1, parent=parent, slot=slot, entry=entry, count=count,
                   flags=1 if item.get("b") else 0, enchants=enchants, prop=max(suffix, 0), durability=durability,
                   charges=charges, duration=proto[9] or 0, name=item.get("n") or proto[1],
                   bag_family=proto[10] or 0, container_slots=proto[11] or 0)
        self.items.append(rec)
        return rec

    def add_container(self, bag, bag_slot, item_base, where, base_size=None):
        """A container: the bag item itself in player slot bag_slot, then its contents (bag=bag guid).
        For the backpack and main bank (item_base set), slots beyond base_size go to self.overflow."""
        parent = None
        if bag_slot is not None:
            if not bag.get("bag"):
                return
            rec = self.add_item(bag["bag"], None, bag_slot, where)
            if not rec:
                return
            parent = rec["key"]
        for slot, item in entries(bag.get("items")):
            if not item:
                continue
            if parent:
                self.add_item(item, parent, int(slot) - 1, where)
            elif base_size and int(slot) > base_size:
                self.overflow.append((item, where))
            else:
                self.add_item(item, None, item_base + int(slot) - 1, where)

    def place_overflow(self):
        """Items with no 1.12 slot (Era's 20-slot backpack, 32-slot keyring): free general-purpose bag slots
        first, then mail."""
        used = {}
        for i in self.items:
            if isinstance(i["parent"], int):
                used.setdefault(i["parent"], set()).add(i["slot"])
        free = []
        for bag in self.items:
            if bag["parent"] is None and 19 <= bag["slot"] <= 22 and bag["container_slots"] and not bag["bag_family"]:
                free += [(bag["key"], s) for s in range(bag["container_slots"]) if s not in used.get(bag["key"], ())]
        moved = mailed = 0
        for item, where in self.overflow:
            if free:
                parent, slot = free.pop(0)
                if self.add_item(item, parent, slot, where):
                    moved += 1
            else:
                rec = self.add_item(item, "mail", 0, where)
                if rec:
                    self.mails.append(dict(subject=f"Restored from {where} (no room on this server)", money=0, item=rec))
                    mailed += 1
        if moved or mailed:
            self.warn(f"{moved + mailed} items had no slot on a 1.12 server (Era's backpack has 20 slots, its keyring 32): "
                      f"{moved} moved to free bag slots, {mailed} sent by mail")

    def build_inventory(self):
        eq = self.s.get("equipped") or {}
        for slot, item in entries(eq):
            if isinstance(slot, int) and 1 <= slot <= 19 and item:
                self.add_item(item, None, slot - 1, "equipped")
        self.ammo = eq.get("ammo") or 0
        self.hide_helm = eq.get("showHelm") is False
        self.hide_cloak = eq.get("showCloak") is False
        bags = self.s.get("bags") or {}
        if not bags:
            self.warn("no bag data")
        self.overflow = []
        backpack = get(bags, 0)
        if backpack:
            self.add_container(backpack, None, 23, "backpack", base_size=16)
        for bag in range(1, 5):
            if get(bags, bag):
                self.add_container(get(bags, bag), 19 + bag - 1, None, f"bag {bag}")
        keyring = get(bags, -2)
        if keyring:
            size = next(n for lvl, n in KEYRING_SIZE if self.level < lvl)
            keys = [item for _, item in entries(keyring.get("items")) if item]
            for n, item in enumerate(keys):
                if n < size:
                    self.add_item(item, None, 81 + n, "keyring")
                else:
                    self.overflow.append((item, "keyring"))
        bank = self.s.get("bank")
        self.bank_slots = 0
        if not bank:
            self.warn("bank never scanned: open the bank once with the addon enabled")
        else:
            if time.time() - bank.get("scannedAt", 0) > STALE_DAYS * 86400:
                self.warn(f"bank scan is {age(bank.get('scannedAt'))}")
            self.bank_slots = bank.get("purchasedSlots") or 0
            containers = bank.get("containers") or {}
            main = get(containers, -1)
            if main:
                self.add_container(main, None, 39, "bank", base_size=24)
            for bag in range(5, 11):
                if get(containers, bag):
                    self.add_container(get(containers, bag), 63 + bag - 5, None, f"bank bag {bag - 4}")
        self.place_overflow()

    def build_mail(self):
        """Every attachment becomes its own mail (1.12 allows one item per mail); money goes on the first."""
        mail = self.s.get("mail")
        if not mail:
            self.warn("mailbox never scanned: open a mailbox once with the addon enabled")
            return
        if (mail.get("total") or 0) > (mail.get("shown") or 0):
            self.warn(f"mailbox held {mail['total']} mails but the client loaded {mail['shown']}; the rest are missing")
        for _, m in entries(mail.get("mails")):
            subject = f"{m.get('sender') or '?'}: {m.get('subject') or ''}"[:120]
            attachments = [i for _, i in entries(m.get("items")) if i]
            first = True
            for item in attachments or [None]:
                rec = self.add_item(item, "mail", 0, "mail") if item else None
                if item and not rec:
                    continue
                money = (m.get("money") or 0) if first else 0
                if rec or money:
                    self.mails.append(dict(subject=subject, money=money, item=rec))
                first = False

    # -- action bars, taxi, exploration
    def build_actions(self):
        self.actions = []
        spells, items = set(self.spells), {i["entry"] for i in self.items}
        for slot, a in entries(self.s.get("actions")):
            if not isinstance(slot, int) or slot > 120 or not a:
                continue
            if a.get("t") == "spell" and a.get("id") in spells:
                self.actions.append((slot - 1, a["id"], 0))
            elif a.get("t") == "item" and a.get("id") in self.ref.items:
                self.actions.append((slot - 1, a["id"], 128))
            elif a.get("t") == "macro":
                m = ui_convert.macro_action(a, self.s, self.db)
                if m:
                    self.actions.append((slot - 1, m[1], m[0]))
                else:
                    self.warn(f"action button {slot}: macro {a.get('macro')!r} isn't among the 1.12 macros")

    # The four extra action bars, as SetActionBarToggles' bits 0-3 (1.12 MultiActionBars.lua, ActionButton.lua):
    # bottom left shows slots 61-72, bottom right 49-60, right 25-36, right 2 37-48.
    BAR_SLOTS = ((0, range(61, 73)), (1, range(49, 61)), (2, range(25, 37)), (3, range(37, 49)))

    def action_bar_bits(self):
        """The extra bars to show: every one that holds an action button (the server keeps this, per character)."""
        used = {button + 1 for button, _, _ in self.actions}
        return sum(1 << bit for bit, slots in self.BAR_SLOTS if used & set(slots))

    def build_ui(self):
        """Character macros (account data section 5) and character-specific key bindings (section 3)."""
        self.ui_data = {}
        if not self.ui:
            return
        macros = ui_convert.seq(self.s.get("macros"))
        if macros:
            text, notes = ui_convert.character_macros(self.s, self.ui.client, self.ui.era)
            self.ui_data[5] = text
            if len(macros) > ui_convert.MAX_MACROS:
                notes.append(f"{len(macros)} character macros; 1.12 holds {ui_convert.MAX_MACROS}")
            for n in notes:
                self.warn(n)
        era = self.ui.era_bindings(self.db, self.realm, self.player.get("name"))
        if era and self.ui.client:
            text, notes = ui_convert.convert_bindings(era, self.ui.client)
            self.ui_data[3] = text
            for n in notes:
                self.warn(f"character key bindings: {n}")
        elif era:
            self.warn("character-specific key bindings need the 1.12 client's Data folder (--wow-data): left out")

    def build_taxi(self):
        taxi = self.s.get("taxi") or {}
        conts = taxi.get("continents") or {}
        for cont, label in ((1415, "Eastern Kingdoms"), (1414, "Kalimdor")):
            if not get(conts, cont):
                self.warn(f"flight paths in {label} unknown: open a flight master there once")
        nodes = set()
        for _, known in entries(taxi.get("nodes")):
            for _, n in entries(known):
                nid = n.get("id") or self.ref.taxi_by_name.get(n.get("name"))
                if nid in self.ref.taxi_ids:
                    nodes.add(nid)
                elif n.get("name"):
                    self.warn(f"flight path {n['name']!r} not on a 1.12 server")
        words = [0] * 8
        for n in nodes:
            words[(n - 1) // 32] |= 1 << ((n - 1) % 32)
        self.taximask = " ".join(map(str, words)) + " "

    def build_explored(self):
        words = [0] * 64
        areas = (self.s.get("explored") or {}).get("areas") or []
        for a in areas:
            row = self.ref.areas.get(a)
            if row and row[3] is not None and 0 <= row[3] < 64 * 32:
                words[row[3] // 32] |= 1 << (row[3] % 32)
        self.explored = " ".join(map(str, words)) + " "
        if not areas:
            self.warn("no exploration data")

    # -- hunter pet
    def build_pet(self):
        self.pet = None
        pet = self.s.get("pet")
        if self.s.get("stable") and len(entries((self.s["stable"] or {}).get("pets"))) > 1:
            self.warn("stabled pets are not imported: the client doesn't reveal which creature they are")
        if not pet or not pet.get("hunter"):
            return
        creature = self.ref.creatures.get(pet.get("creatureID"))
        if not creature or creature[3] != 1:
            self.warn(f"pet {pet.get('name')}: creature {pet.get('creatureID')} isn't a 1.12 beast, not imported")
            return
        loyalty = pet.get("loyalty") or 1
        tp = pet.get("trainingPoints") or {}
        spells = []
        for s in pet.get("spells") or []:
            if s.get("id") in self.ref.spells:
                spells.append((s["id"], {True: 193, False: 129}.get(s.get("autocast"), 0)))
        self.pet = dict(entry=creature[0], display=creature[2], level=min(pet.get("level") or 1, self.level),
                        xp=pet.get("xp") or 0, loyalty=loyalty, loyalty_points=LOYALTY_START[loyalty],
                        training=(tp.get("total") or 0) - (tp.get("spent") or 0), name=pet.get("name") or "Pet",
                        happiness=HAPPINESS.get(pet.get("happiness"), 499500), spells=spells)

    # -- riding
    def build_riding(self):
        """Era keeps the race riding skills (Wolf Riding, Raptor Riding...); 1.12 mount items on both cores need
        Riding (762) at 75 or 150. Convert like vmangos's Player::UpdateOldRidingSkillToNew: drop the race
        skills, add Riding, and teach Apprentice (33388) or, with an epic mount, Journeyman (33391) Riding.
        cmangos has no such conversion, so it is done here for both cores."""
        if any(sid == RIDING for sid, _, _ in self.skills):
            return
        old = [(sid, value) for sid, value, _ in self.skills if sid in RACE_RIDING]
        spells = set(self.spells)
        class_mount = spells & {13819, 5784}  # Paladin / Warlock level 40 mount
        class_epic = spells & {23214, 23161}  # Paladin / Warlock level 60 mount
        if not old and not class_mount and not class_epic:
            return
        epic_items = [i["name"] for i in self.items if self.ref.mount_rank.get(i["entry"]) == 150]
        epic = bool(epic_items or class_epic)
        self.skills = [s for s in self.skills if s[0] not in RACE_RIDING]
        self.skills.append((RIDING, 150 if epic else 75, 150 if epic else 75))
        for spell in ([33388, 33391] if epic else [33388]):
            if spell not in spells and spell in self.ref.spells:
                self.spells.append(spell)
        self.warn(f"riding: Era's race skills ({', '.join(str(s) for s, _ in old) or 'class mount'}) converted to "
                  f"Riding {150 if epic else 75}" + (f" (epic mount: {epic_items[0]})" if epic_items else ""))

    # -- honor
    def build_honor(self):
        h = self.player.get("honor") or {}
        self.hk, self.dk = h.get("hk") or 0, h.get("dk") or 0
        self.highest_rank = h.get("highestRank") or 0
        visual = max((h.get("rank") or 0) - 4, 0)
        p = h.get("progress") or 0
        if visual == 0:
            self.rank_points = 0.0
        elif visual == 1:
            self.rank_points = max(1.0, 2000 * p)
        else:
            low, high = max(2000, (visual - 2) * 5000), (visual - 1) * 5000
            self.rank_points = low + p * (high - low)

    # -- common derived values
    def played(self):
        played = self.player.get("played") or {}
        total = played.get("total") or 0
        if not total:
            self.warn("played time unknown; using 1 second")
        return max(total, 1), max(played.get("level") or 0, 0)

    def equipment_cache(self):
        by_slot = {i["slot"]: i for i in self.items if i["parent"] is None and i["slot"] <= 19}
        tokens = []
        for slot in range(20):
            i = by_slot.get(slot)
            tokens += [i["entry"], i["enchants"][0][0] if slot < 19 else 0] if i else [0, 0]
        return " ".join(map(str, tokens)) + " "


# ---------------------------------------------------------------------------------------------- SQL

def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, Raw):
        return v
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


class Raw(str):
    """An SQL expression such as @guid or @item+3, inserted unquoted."""


def insert(table, row):
    cols = ", ".join(f"`{c}`" for c in row)
    return f"INSERT INTO `{table}` ({cols}) VALUES ({', '.join(q(v) for v in row.values())});"


def enchant_string(e):
    return " ".join(str(x) for slot in e for x in slot) + " "


def render_ui_only(b, core):
    """For a character already on the server: its macros and bindings (replaced) and its macro buttons (only
    into empty action slots, so nothing set up since is touched)."""
    out = [f"-- CharSnapshot macros and key bindings of {b.name}, for a character already on the server",
           f"SET @guid = (SELECT guid FROM characters WHERE name = {q(b.name)});",
           f"SELECT IF(@guid IS NULL, 'WARNING: no character named {b.name}', 'character found') AS check_name;"]
    for kind, text in sorted(b.ui_data.items()):
        out.append(f"REPLACE INTO `character_account_data` (`guid`, `type`, `time`, `data`) "
                   f"SELECT @guid, {kind}, {b.now}, {q(text)} FROM DUAL WHERE @guid IS NOT NULL;")
    bars = b.action_bar_bits()
    if bars:  # show the bars that hold buttons, keeping any shown since
        col = "actionBars" if core == "cmangos" else "action_bars"
        out.append(f"UPDATE `characters` SET `{col}` = `{col}` | {bars} WHERE guid = @guid;")
    for button, action, kind in b.actions:
        if kind in (ui_convert.ACTION_MACRO, ui_convert.ACTION_CMACRO):
            out.append(f"INSERT IGNORE INTO `character_action` (`guid`, `button`, `action`, `type`) "
                       f"SELECT @guid, {button}, {action}, {kind} FROM DUAL WHERE @guid IS NOT NULL;")
    return "\n".join(out) + "\n"


def render_account_data(db, ui, account, replace):
    """Account macros (section 4) and key bindings (section 2)."""
    notes, rows = [], {}
    if ui_convert.seq((db.get("accountMacros") or {}).get("list")):
        rows[4], n = ui_convert.account_macros(db, ui.client, ui.era)
        notes += n
    era = ui.era_bindings(db)
    if era and ui.client:
        rows[2], n = ui_convert.convert_bindings(era, ui.client)
        notes += n
    elif era:
        notes.append("key bindings need the 1.12 client's Data folder (--wow-data): left out")
    verb = "REPLACE" if replace else "INSERT IGNORE"
    out = ["-- CharSnapshot account macros and key bindings" + ("" if replace else " (kept if the account has some)")]
    for kind, text in sorted(rows.items()):
        out.append(f"{verb} INTO `account_data` (`account`, `type`, `time`, `data`) "
                   f"VALUES ({account}, {kind}, {int(time.time())}, {q(text)});")
    return "\n".join(out) + "\n", notes


def render(b, core, account, rename):
    g, now = Raw("@guid"), b.now
    item_ref = lambda key: Raw(f"@item+{key}")
    total, level_time = b.played()
    rest = (b.player.get("rested") or 0) / 2  # the client shows twice the server's rest pool
    hp, mana = 0x7FFFFFFF, 0x7FFFFFFF
    out = [f"-- CharSnapshot import of {b.player['name']} ({b.player.get('realm')}) for {core}",
           f"-- generated {time.strftime('%Y-%m-%d %H:%M')} from a snapshot saved "
           f"{time.strftime('%Y-%m-%d %H:%M', time.localtime((b.s.get('meta') or {}).get('savedAt', 0)))}",
           "-- Run against the characters database with the world server STOPPED.",
           *(["-- vmangos stores most character tables as MyISAM, which ignores transactions: a failed or rolled-back",
              "-- import leaves partial rows behind. Back up the characters database first (mysqldump characters)."]
             if core == "vmangos" else
             ["-- The transaction protects the import: cmangos character tables are InnoDB."]), ""]
    for w in b.warnings:
        out.append(f"-- warning: {w}")
    out += ["", f"SELECT IF(COUNT(*) > 0, 'WARNING: name {b.name} is taken, use --name or --rename', 'name ok') AS check_name "
            f"FROM characters WHERE name = {q(b.name)};",
            f"SET @acct = {account};",
            "SET @guid = (SELECT COALESCE(MAX(guid), 0) + 1 FROM characters);",
            "SET @item = (SELECT COALESCE(MAX(guid), 0) FROM item_instance);",
            "SET @mail = (SELECT COALESCE(MAX(id), 0) FROM mail);",
            "SET @pet = (SELECT COALESCE(MAX(id), 0) + 1 FROM character_pet);",
            "START TRANSACTION;", ""]
    skin, face, hair, haircolor, facial = b.appearance
    resting = bool(b.player.get("resting"))
    common_honor_rank = b.highest_rank
    if core == "cmangos":
        player_flags = (0x400 if b.hide_helm else 0) | (0x800 if b.hide_cloak else 0)
        rest_state = 1 if rest > 10 else 2
        out.append(insert("characters", {
            "guid": g, "account": Raw("@acct"), "name": b.name, "race": b.race, "class": b.cls, "gender": b.gender,
            "level": b.level, "xp": b.player.get("xp") or 0, "money": b.player.get("money") or 0,
            "playerBytes": skin | face << 8 | hair << 16 | haircolor << 24,
            "playerBytes2": facial | b.bank_slots << 16 | rest_state << 24,
            "playerFlags": player_flags, "position_x": b.pos[1], "position_y": b.pos[2], "position_z": b.pos[3],
            "map": b.pos[0], "orientation": b.pos[4], "taximask": b.taximask, "online": 0, "cinematic": 1,
            "totaltime": total, "leveltime": level_time, "logout_time": now, "is_logout_resting": int(resting),
            "rest_bonus": rest, "at_login": 1 if rename else 0, "zone": b.zone,
            "honor_highest_rank": common_honor_rank, "honor_standing": 0, "stored_honor_rating": b.rank_points,
            "stored_dishonorable_kills": b.dk, "stored_honorable_kills": b.hk,
            "watchedFaction": b.watched if b.watched is not None else 4294967295,
            "health": hp, "power1": mana, "power2": 0, "power3": 0, "power4": 0x7FFFFFFF, "power5": 0,
            "exploredZones": b.explored, "equipmentCache": b.equipment_cache(), "ammoId": b.ammo,
            "actionBars": b.action_bar_bits(),
            "stable_slots": min((b.s.get("stable") or {}).get("slots") or 0, 2),
        }))
    else:
        flags = ((0x2 if resting else 0) | (0x400 if b.hide_helm else 0) | (0x800 if b.hide_cloak else 0)
                 | (0x4000 if rename else 0))
        # Not 0x200000 (MOUNT_UPGRADED): without it vmangos converts Era's race riding skills (Wolf Riding...)
        # to Riding at first login and teaches Apprentice/Journeyman Riding, which 1.12 mount items require.
        out.append(insert("characters", {
            "guid": g, "account": Raw("@acct"), "name": b.name, "race": b.race, "class": b.cls, "gender": b.gender,
            "skin": skin, "face": face, "hair_style": hair, "hair_color": haircolor, "facial_hair": facial,
            "level": b.level, "xp": b.player.get("xp") or 0, "money": b.player.get("money") or 0,
            "character_flags": flags, "zone": b.zone, "map": b.pos[0], "instance": 0,
            "position_x": b.pos[1], "position_y": b.pos[2], "position_z": b.pos[3], "orientation": b.pos[4],
            "known_taxi_mask": b.taximask, "online": 0, "played_time_total": total, "played_time_level": level_time,
            "create_time": now, "logout_time": now, "rest_bonus": rest,
            "stable_slots": min((b.s.get("stable") or {}).get("slots") or 0, 2), "bank_bag_slots": b.bank_slots,
            "extra_flags": 0x4, "honor_rank_points": b.rank_points, "honor_highest_rank": common_honor_rank,
            "honor_stored_hk": b.hk, "honor_stored_dk": b.dk,
            "watched_faction": b.watched if b.watched is not None else -1,
            "health": hp, "power1": mana, "power2": 0, "power3": 0, "power4": 0x7FFFFFFF, "power5": 0,
            "explored_zones": b.explored, "equipment_cache": b.equipment_cache(), "ammo_id": b.ammo,
            "action_bars": b.action_bar_bits(), "world_phase_mask": 1,
        }))

    if b.homebind:
        m, area, x, y, z = b.homebind
        out.append(insert("character_homebind", {"guid": g, "map": m, "zone": area, "position_x": x,
                                                 "position_y": y, "position_z": z}))
    for s in b.spells:
        out.append(insert("character_spell", {"guid": g, "spell": s, "active": 1, "disabled": 0}))
    for sid, value, mx in b.skills:
        out.append(insert("character_skills", {"guid": g, "skill": sid, "value": value, "max": mx}))
    for fid, standing, flags in b.reps:
        out.append(insert("character_reputation", {"guid": g, "faction": fid, "standing": standing, "flags": flags}))
    for qd in b.quests:
        mobs = qd.get("mobs", [0, 0, 0, 0])
        row = {"guid": g, "quest": qd["id"], "status": qd["status"], "rewarded": qd["rewarded"],
               "explored": qd.get("explored", 0), "timer": qd.get("timer", 0)}
        prefix = "mobcount" if core == "cmangos" else "mob_count"
        for i in range(4):
            row[f"{prefix}{i + 1}"] = mobs[i]
        out.append(insert("character_queststatus", row))
    for button, action, kind in b.actions:
        out.append(insert("character_action", {"guid": g, "button": button, "action": action, "type": kind}))
    for kind, text in sorted(b.ui_data.items()):  # macros / key bindings, which the client downloads at login
        out.append(insert("character_account_data", {"guid": g, "type": kind, "time": now, "data": text}))

    # Items: containers before contents is not required (the server loads ORDER BY bag, slot).
    for it in b.items:
        guid = item_ref(it["key"])
        common = dict(count=it["count"], duration=it["duration"], charges=" ".join(map(str, it["charges"])) + " ",
                      flags=it["flags"], enchantments=enchant_string(it["enchants"]), durability=it["durability"])
        if core == "cmangos":
            out.append(insert("item_instance", {
                "guid": guid, "owner_guid": g, "itemEntry": it["entry"], "creatorGuid": 0, "giftCreatorGuid": 0,
                **common, "randomPropertyId": it["prop"], "itemTextId": 0}))
        else:
            out.append(insert("item_instance", {
                "guid": guid, "item_id": it["entry"], "owner_guid": g, "creator_guid": 0, "gift_creator_guid": 0,
                **common, "random_property_id": it["prop"], "text": 0, "generated_loot": 0}))
        if it["parent"] == "mail":
            continue
        bag = item_ref(it["parent"]) if it["parent"] else 0
        if core == "cmangos":
            out.append(insert("character_inventory", {"guid": g, "bag": bag, "slot": it["slot"], "item": guid,
                                                      "item_template": it["entry"]}))
        else:
            out.append(insert("character_inventory", {"guid": g, "bag": bag, "slot": it["slot"], "item_guid": guid,
                                                      "item_id": it["entry"]}))

    for n, m in enumerate(b.mails, 1):
        mail_id = Raw(f"@mail+{n}")
        it = m["item"]
        if core == "cmangos":
            out.append(insert("mail", {
                "id": mail_id, "messageType": 0, "stationery": 41, "mailTemplateId": 0, "sender": g, "receiver": g,
                "subject": m["subject"], "itemTextId": 0, "has_items": 1 if it else 0, "expire_time": now + 30 * 86400,
                "deliver_time": now, "money": m["money"], "cod": 0, "checked": 0}))
            if it:
                out.append(insert("mail_items", {"mail_id": mail_id, "item_guid": item_ref(it["key"]),
                                                 "item_template": it["entry"], "receiver": g}))
        else:
            out.append(insert("mail", {
                "id": mail_id, "message_type": 0, "stationery": 41, "mail_template_id": 0, "sender_guid": g,
                "receiver_guid": g, "subject": m["subject"], "item_text_id": 0, "has_items": 1 if it else 0,
                "expire_time": now + 30 * 86400, "deliver_time": now, "money": m["money"], "cod": 0, "checked": 0}))
            if it:
                out.append(insert("mail_items", {"mail_id": mail_id, "item_guid": item_ref(it["key"]),
                                                 "item_id": it["entry"], "receiver_guid": g}))

    if b.pet:
        p, pid = b.pet, Raw("@pet")
        if core == "cmangos":
            out.append(insert("character_pet", {
                "id": pid, "entry": p["entry"], "owner": g, "modelid": p["display"], "CreatedBySpell": HUNTER_TAME_SPELL,
                "PetType": 1, "level": p["level"], "exp": p["xp"], "Reactstate": 1,
                "loyaltypoints": p["loyalty_points"], "loyalty": p["loyalty"], "xpForNextLoyalty": 0,
                "trainpoint": p["training"], "name": p["name"], "renamed": 1, "slot": 0, "curhealth": 0x7FFFFFFF,
                "curmana": 100, "curhappiness": p["happiness"], "savetime": now, "resettalents_cost": 0,
                "resettalents_time": 0, "abdata": "", "teachspelldata": "0 0 0 0 0 0 0 0 "}))
        else:
            out.append(insert("character_pet", {
                "id": pid, "entry": p["entry"], "owner_guid": g, "display_id": p["display"],
                "created_by_spell": HUNTER_TAME_SPELL, "pet_type": 1, "level": p["level"], "xp": p["xp"],
                "react_state": 1, "loyalty_points": p["loyalty_points"], "loyalty": p["loyalty"],
                "training_points": p["training"], "name": p["name"], "renamed": 1, "slot": 0,
                "current_health": 0x7FFFFFFF, "current_mana": 0, "current_happiness": p["happiness"],
                "save_time": now, "reset_talents_cost": 0, "reset_talents_time": 0,
                "action_bar_data": VMANGOS_PET_BAR, "teach_spell_data": "0 0 0 0 0 0 0 0 "}))
        for spell, active in p["spells"]:
            out.append(insert("pet_spell", {"guid": pid, "spell": spell, "active": active}))

    out += ["", "COMMIT;", "",
            "-- Afterwards, in the realmd/logon database (realm list character count, cosmetic):",
            "--   DELETE FROM realmcharacters WHERE acctid = <account> AND realmid = <realm id>;",
            "--   INSERT INTO realmcharacters (numchars, acctid, realmid)",
            "--     SELECT COUNT(*), <account>, <realm id> FROM <characters db>.characters WHERE account = <account>;"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------- commands

def cmd_list(_):
    for name, realm, snap, _ in characters():
        p = snap.get("player") or {}
        print(f"{name}-{realm}: level {p.get('level')} {p.get('race')} {p.get('class')}, "
              f"full scan {age(snap.get('lastFullScan'))}, bank {age((snap.get('bank') or {}).get('scannedAt'))}, "
              f"mail {age((snap.get('mail') or {}).get('scannedAt'))}")


def cmd_check(args):
    name, realm, snap, db = find(args.name)
    b = Build(snap, Ref(), db=db, ui=UI(args.wow_data), realm=realm)
    p = b.player
    print(f"{name}-{realm}: level {p['level']} {p.get('race')} {p.get('class')}, "
          f"{(p.get('money') or 0) / 10000:.2f}g, saved {age((snap.get('meta') or {}).get('savedAt'))}")
    where = {"equipped/bags/bank": sum(1 for i in b.items if i["parent"] != "mail"),
             "mail": sum(1 for i in b.items if i["parent"] == "mail")}
    print(f"  items: {where}, spells {len(b.spells)} (talent ranks {len(b.talent_spells)}), skills {len(b.skills)}, "
          f"reputations {len(b.reps)}, quests {len(b.quests)}, action buttons {len(b.actions)}, "
          f"pet {'yes' if b.pet else 'no'}")
    for e in snap.get("errors") or []:
        print(f"  addon error {time.strftime('%Y-%m-%d', time.localtime(e.get('t', 0)))} {e.get('where')}: {e.get('err')}")
    for w in b.warnings:
        print(f"  - {w}")


def cmd_json(args):
    name, realm, snap, db = find(args.name)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}-{realm}.json"
    path.write_text(json.dumps({"character": snap, "accountMacros": db.get("accountMacros")}, indent=1,
                               ensure_ascii=False, default=str))
    print(path)


def cmd_sql(args):
    name, realm, snap, db = find(args.name)
    appearance = [int(x) for x in args.appearance.split(",")] if args.appearance else None
    if appearance and len(appearance) != 5:
        raise SystemExit("--appearance needs 5 numbers: skin,face,hairstyle,haircolor,facialhair")
    b = Build(snap, Ref(), args.rename_to, appearance, db=db, ui=UI(args.wow_data), realm=realm)
    sql = render_ui_only(b, args.core) if args.ui_only else render(b, args.core, args.account, args.rename)
    path = Path(args.output) if args.output else OUT / f"{name}-{realm}.{args.core}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql)
    print(path)
    for w in b.warnings:
        print(f"  - {w}")


def cmd_account_data(args):
    dbs = {id(db): db for _, _, _, db in characters()}
    if len(dbs) != 1:
        raise SystemExit(f"{len(dbs)} accounts with CharSnapshot data; this handles one (set WOW_CLASSIC_ERA)"
                         if dbs else "no CharSnapshot data found")
    sql, notes = render_account_data(next(iter(dbs.values())), UI(args.wow_data), args.account, args.replace)
    path = Path(args.output) if args.output else OUT / f"account-data.{args.core}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql)
    print(path)
    for n in notes:
        print(f"  - {n}")


def cmd_addon_data(_):
    """Candidate spells the addon checks with IsSpellKnown: every skill-line ability and talent rank."""
    c = sqlite3.connect(CLIENT_DB)
    ids = {r[0] for r in c.execute("SELECT Spell FROM SkillLineAbility WHERE Spell > 0")}
    ids |= {s for r in c.execute("SELECT SpellRank_0, SpellRank_1, SpellRank_2, SpellRank_3, SpellRank_4, "
                                 "SpellRank_5, SpellRank_6, SpellRank_7, SpellRank_8 FROM Talent") for s in r if s}
    build = c.execute("SELECT value FROM _meta WHERE key = 'build'").fetchone()[0]
    ids = sorted(ids)
    lines = [f"-- Generated by tools/char_snapshot.py addon-data from the Era client DB (build {build}).",
             "-- Spell IDs the addon checks with IsSpellKnown: skill-line abilities (recipes, ranks, proficiencies)",
             "-- and talent ranks, which the spellbook doesn't list.", "local _, ns = ...", "ns.Data = { spells = {"]
    for i in range(0, len(ids), 16):
        lines.append("  " + ", ".join(map(str, ids[i:i + 16])) + ",")
    lines += ["} }", ""]
    (ADDON / "Data.lua").write_text("\n".join(lines))
    print(f"{ADDON / 'Data.lua'}: {len(ids)} spells")


def cmd_lint(_):
    """Registering an unknown event raises an error in the client and aborts loading the file, so every
    event name the addon uses must exist in Era's API docs (Gethe/wow-ui-source, via tools/era_api.py)."""
    import re
    import era_api
    valid = {e.get("LiteralName") for d in era_api.load() for e in (d.get("Events") or [])}
    used = {}
    for f in sorted(ADDON.glob("*.lua")):
        text = f.read_text()
        for name in re.findall(r'CB\.On\("([A-Z_]+)"', text) + re.findall(r"^\s+([A-Z][A-Z_]+) = \{ \"", text, re.M):
            used.setdefault(name, f.name)
    bad = sorted(n for n in used if n not in valid)
    for n in bad:
        print(f"{used[n]}: event {n} is not in the Era API docs")
    print(f"{len(used)} events checked, {len(bad)} unknown")
    if bad:
        sys.exit(1)


def cmd_install(_):
    target = era_dir() / "Interface" / "AddOns" / "CharSnapshot"
    if target.is_symlink() or target.exists():
        print(f"{target} already exists ({'symlink to ' + os.readlink(target) if target.is_symlink() else 'folder'})")
        return
    target.symlink_to(ADDON)
    print(f"{target} -> {ADDON}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    wow_data_help = "the 1.12.1 client's Data folder (default $WOW_DATA), for key bindings and macro icons"
    for cmd, fn in (("check", cmd_check), ("json", cmd_json)):
        p = sub.add_parser(cmd)
        p.add_argument("name")
        p.add_argument("--wow-data", help=wow_data_help)
        p.set_defaults(fn=fn)
    p = sub.add_parser("sql")
    p.add_argument("name")
    p.add_argument("--core", choices=("cmangos", "vmangos"), required=True)
    p.add_argument("--account", type=int, required=True, help="realmd/logon account.id to own the character")
    p.add_argument("--name", dest="rename_to", help="import under a different name")
    p.add_argument("--rename", action="store_true", help="force a rename at first login (name taken)")
    p.add_argument("--appearance", help="skin,face,hairstyle,haircolor,facialhair (not readable by addons)")
    p.add_argument("--ui-only", action="store_true",
                   help="only macros, macro buttons (into empty slots) and key bindings, for a character already there")
    p.add_argument("--wow-data", help=wow_data_help)
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_sql)
    p = sub.add_parser("account-data", help="account-wide macros and key bindings as SQL")
    p.add_argument("--core", choices=("cmangos", "vmangos"), required=True)
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--replace", action="store_true", help="overwrite the account's macros and key bindings")
    p.add_argument("--wow-data", help=wow_data_help)
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_account_data)
    sub.add_parser("addon-data").set_defaults(fn=cmd_addon_data)
    sub.add_parser("lint").set_defaults(fn=cmd_lint)
    sub.add_parser("install").set_defaults(fn=cmd_install)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
