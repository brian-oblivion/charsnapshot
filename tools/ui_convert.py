#!/usr/bin/env python3
"""Era macros and key bindings, converted to the 1.12 client's own files.

1.12 keeps them in the same files as Era, in older formats, and the server keeps a copy of each ("account
data", which both vmangos and cmangos store in account_data / character_account_data and send to the client
at login):

  section 2  account   bindings-cache.wtf   the whole key table, `bind <key> <command>` per line
  section 4  account   macros-cache.txt     `MACRO <id> "<name>" <icon>`, body lines, `END`
  section 5  character macros-cache.txt

What 1.12 lacks, and what this does about it:
  - Macros: 18 account + 18 character ones (Era: 120 + 18). Account macros used on an action bar go first.
  - No macro conditionals ([@mouseover,help] ...), no /use, /stopcasting, /cleartarget, /startattack,
    /castsequence, /cancelaura. Most become /script lines with the 1.12 API (UseInventoryItem,
    SpellStopCasting, CastSpellByName, TargetUnit...) when the result fits the 255-character body limit;
    the rest are left out of the macro and reported.
  - #showtooltip doesn't exist (a line without / would be said in chat): dropped. A question-mark icon
    becomes the icon of the macro's spell when there is one.
  - Icons are stored by texture name, not file ID: Era's file IDs are looked up in the Era client's
    ManifestInterfaceData and checked against the icons the 1.12 client has.
  - Era's bindings-cache.wtf lists only changes from Era's defaults; 1.12's must hold the whole table. The
    changes are applied to 1.12's defaults (WTF\\DefaultBindings.wtf in the 1.12 client), and commands 1.12
    doesn't have (Era-only actions, addons) are left out.

The 1.12 data (defaults, command list, icon list, slash commands) is read from the 1.12 client's MPQs.
"""
import re
import sqlite3
from pathlib import Path

import mpq
from lua_data import entries

MAX_MACROS = 18
CHARACTER_ID_BASE = 0x1000000  # 1.12 character macro ids start after this; account ones at 1
MAX_BODY = 255
QUESTION_MARK = 134400  # inv_misc_questionmark's file ID
ACTION_MACRO, ACTION_CMACRO = 0x40, 0x41  # character_action.type for account / character macros


class Client112:
    """What the 1.12 client has, read from its Data folder."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        read = lambda name: (mpq.read_from_chain(self.data_dir, name) or b"").decode("utf-8", "replace")
        self.default_bindings = []
        for line in read("WTF\\DefaultBindings.wtf").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0].lower() == "bind":
                self.default_bindings.append((parts[1], parts[2]))
        if not self.default_bindings:
            raise SystemExit(f"no WTF\\DefaultBindings.wtf in {data_dir}: is it a 1.12.1 client's Data folder?")
        self.commands = set(re.findall(r'<Binding\s+name="([^"]+)"', read("Interface\\FrameXML\\Bindings.xml")))
        self.slash = {m.lower() for m in re.findall(r'^SLASH_[A-Z_]+\d+\s*=\s*"(/[^"]+)"',
                                                    read("Interface\\FrameXML\\GlobalStrings.lua"), re.M)}
        self.icons = {}
        for archive in mpq.CHAIN:
            path = self.data_dir / archive
            listfile = mpq.Archive(path).read("(listfile)") if path.exists() else None
            for line in (listfile or b"").decode("latin-1").splitlines():
                low = line.lower()
                if low.startswith("interface\\icons\\") and low.endswith(".blp"):
                    self.icons.setdefault(low[16:-4], line[16:-4])


class EraData:
    """Era icon file names and spell icons, from the Era client tables (tools/fetch-data.sh)."""

    def __init__(self, client_db):
        c = sqlite3.connect(client_db)
        self.files = {i: n.rsplit(".", 1)[0] for i, n in c.execute(
            "SELECT ID, FileName FROM ManifestInterfaceData WHERE lower(FilePath) = 'interface\\icons\\'")}
        self.spell_icon = {}
        for name, icon in c.execute("SELECT n.Name_lang, m.SpellIconFileDataID FROM SpellName n "
                                    "JOIN SpellMisc m ON m.SpellID = n.ID WHERE m.SpellIconFileDataID > 0"):
            self.spell_icon.setdefault(name.lower(), icon)


def seq(t):
    """A parsed Lua array as a Python list, whether it came back as a list or a dict with 1..n keys."""
    return [v for _, v in sorted(entries(t), key=lambda kv: kv[0] if isinstance(kv[0], int) else 0)]


# ---------------------------------------------------------------------------------------------- macro bodies

def lua_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


COND_UNIT_FREE = {
    "combat": 'UnitAffectingCombat("player")', "nocombat": 'not UnitAffectingCombat("player")',
    "pet": 'UnitExists("pet")', "nopet": 'not UnitExists("pet")',
    "mod": "(IsShiftKeyDown()or IsControlKeyDown()or IsAltKeyDown())",
    "nomod": "not(IsShiftKeyDown()or IsControlKeyDown()or IsAltKeyDown())",
    "mod:shift": "IsShiftKeyDown()", "mod:ctrl": "IsControlKeyDown()", "mod:alt": "IsAltKeyDown()",
    "nomod:shift": "not IsShiftKeyDown()", "nomod:ctrl": "not IsControlKeyDown()", "nomod:alt": "not IsAltKeyDown()",
}
UNITS = {"player", "target", "mouseover", "pet", "targettarget", "party1", "party2", "party3", "party4"}


def parse_options(text):
    """`[a,b][c] rest` -> ([[a, b], [c]], rest); None for a syntax we don't handle."""
    groups = []
    text = text.strip()
    while text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        groups.append([o.strip().lower() for o in text[1:end].split(",") if o.strip()])
        text = text[end + 1:].strip()
    return groups, text


def option_expr(opts):
    """One [..] group -> (unit, Lua condition or "") ; None when an option has no 1.12 equivalent."""
    unit, conds = "target", []
    for o in opts:
        if o.startswith("@") or o.startswith("target="):
            unit = o.split("=", 1)[1] if "=" in o else o[1:]
            if unit not in UNITS:
                return None
    for o in opts:
        if o.startswith("@") or o.startswith("target="):
            continue
        u = lua_str(unit)
        if o == "help":
            conds.append(f"UnitIsFriend(\"player\",{u})")
        elif o == "harm":
            conds.append(f"UnitCanAttack(\"player\",{u})")
        elif o == "dead":
            conds.append(f"UnitIsDeadOrGhost({u})")
        elif o in ("nodead", "alive"):
            conds.append(f"not UnitIsDeadOrGhost({u})")
        elif o == "exists":
            pass
        elif o == "noexists":
            conds.append(f"not UnitExists({u})")
        elif o in COND_UNIT_FREE:
            conds.append(COND_UNIT_FREE[o])
        else:
            return None
    # UnitIsFriend / UnitCanAttack are nil for a unit that doesn't exist, so they need no UnitExists.
    checked = any(o in ("help", "harm") for o in opts)
    if unit != "player" and not checked and not any(c.startswith("not UnitExists") for c in conds) and \
            (any(o in ("dead", "nodead", "alive", "exists") for o in opts) or unit != "target"):
        conds.insert(0, f"UnitExists({lua_str(unit)})")
    return unit, "and ".join(conds)


def cast_on(unit, spell):
    s = lua_str(spell)
    if unit == "target":
        return f"CastSpellByName({s})"
    if unit == "player":
        return f"CastSpellByName({s},1)"
    u = lua_str(unit)
    return f"if UnitIsUnit({u},\"target\")then CastSpellByName({s})else TargetUnit({u})CastSpellByName({s})TargetLastTarget()end"


def unit_template(unit, cond):
    """A branch condition written for any unit `u`, for the shared-helper form; None if it can't be."""
    if not cond:
        return ""
    return cond.replace(lua_str(unit), "u") if unit not in ("player",) else None


def one_spell_script(branches):
    """All branches cast one spell: pick the unit, then cast once. The short form."""
    spell = branches[0][2]
    units = [u for u, _, _ in branches]
    templates = [unit_template(u, c) for u, c, _ in branches]
    shared = next((t for t in templates if t), None)
    helper = ""
    parts = []
    for (unit, cond, _), tmpl in zip(branches, templates):
        if not cond:
            parts.append(lua_str(unit))
            break
        if shared and tmpl == shared and unit != "player" and templates.count(shared) > 1:
            helper = f"local function c(u)return {shared} end "
            parts.append(f"c({lua_str(unit)})and{lua_str(unit)}")
        else:
            parts.append(f"{cond}and{lua_str(unit)}")
    chain = "or ".join(parts)
    if set(units) <= {"target", "player"}:
        cast = 'CastSpellByName(s,u=="player"and 1 or nil)'
    else:
        cast = 'if u=="target"then CastSpellByName(s)else TargetUnit(u)CastSpellByName(s)TargetLastTarget()end'
    tail = "" if not branches[-1][1] or not branches[-1][1] else " if u then"
    script = f"{helper}local s={lua_str(spell)} local u={chain}"
    return "/script " + script + (f"{tail} {cast} end" if tail else f" {cast}")


def convert_cast(args, short_units=None):
    """`/cast [opts] A; [opts] B` -> a /script line, or None. short_units keeps only branches on those units."""
    branches = []
    for clause in args.split(";"):
        parsed = parse_options(clause)
        if not parsed or not parsed[1]:
            return None
        groups, spell = parsed
        for opts in groups or [[]]:
            r = option_expr(opts)
            if r is None:
                return None
            if short_units is None or r[0] in short_units:
                branches.append((r[0], r[1], spell))
    if not branches:
        return None
    if len({s for _, _, s in branches}) == 1:
        return one_spell_script(branches)
    branches = [(c, cast_on(u, s)) for u, c, s in branches]
    script = ""
    for i, (cond, action) in enumerate(branches):
        if not cond:  # always true: the last branch that can run
            script += (" else " if i else "") + action
            break
        script += ("if " if i == 0 else " elseif ") + cond + " then " + action
    if branches[0][0]:
        script += " end"
    return "/script " + script


def bag_use(name):
    n = lua_str(name)
    return (f"/script for b=0,4 do for s=1,GetContainerNumSlots(b)do local l=GetContainerItemLink(b,s)"
            f"if l and strfind(l,\"%[\"..{n}..\"%]\")then UseContainerItem(b,s)return end end end")


SIMPLE = {  # Era-only command -> 1.12 script
    "/stopcasting": "/script SpellStopCasting()",
    "/cleartarget": "/script ClearTarget()",
    "/startattack": "/script if not PlayerFrame.inCombat then AttackTarget()end",
    "/stopattack": "/script if PlayerFrame.inCombat then AttackTarget()end",
    "/targetlasttarget": "/script TargetLastTarget()",
    "/targetenemy": "/script TargetNearestEnemy()",
    "/targetfriend": "/script TargetNearestFriend()",
    "/petattack": "/script PetAttack()",
    "/petfollow": "/script PetFollow()",
    "/petstay": "/script PetWait()",
    "/petpassive": "/script PetPassiveMode()",
    "/petdefensive": "/script PetDefensiveMode()",
    "/petaggressive": "/script PetAggressiveMode()",
}
NO_EQUIVALENT = {"/castsequence", "/castrandom", "/userandom", "/cancelaura", "/cancelform", "/equip", "/equipslot",
                 "/focus", "/clearfocus", "/dismount", "/stopmacro", "/click", "/makgora", "/targetparty",
                 "/targetraid", "/petautocaston", "/petautocastoff"}


def convert_line(line, client):
    """One Era macro line -> (1.12 line or None, note or None)."""
    stripped = line.strip()
    if not stripped:
        return None, None
    if stripped.startswith("#"):
        return None, None  # #showtooltip / #show: 1.12 would say the line in chat
    if not stripped.startswith("/"):
        return line, None
    cmd, _, args = stripped.partition(" ")
    low = cmd.lower()
    if low in ("/cast", "/spell"):
        if "[" not in args and ";" not in args:
            return f"/cast {args.strip()}", None
        out = convert_cast(args)
        if out and len(out) > MAX_BODY:  # mouseover and friends cost the most: keep target/self casting
            short = convert_cast(args, short_units={"target", "player"})
            if short and len(short) <= MAX_BODY:
                return short, f"{stripped!r}: too long for 1.12 with the mouseover part, kept target/self only"
        return (out, None) if out else (None, f"{stripped!r}: conditionals with no 1.12 equivalent")
    if low == "/use":
        a = args.strip()
        if "[" in a or ";" in a:
            return None, f"{stripped!r}: /use with conditionals"
        if re.fullmatch(r"\d{1,2}", a):
            return f"/script UseInventoryItem({a})", None
        if re.fullmatch(r"\d \d{1,2}", a):
            b, s = a.split()
            return f"/script UseContainerItem({b},{s})", None
        return bag_use(a), None
    if low in SIMPLE and not args.strip():
        return SIMPLE[low], None
    if low == "/targetexact" and "[" not in args:
        return f"/target {args.strip()}", None  # 1.12's /target matches by name, nearest first
    if low in ("/target", "/tar", "/assist", "/a") and "[" in args:
        return None, f"{stripped!r}: conditionals with no 1.12 equivalent"
    if low in NO_EQUIVALENT or low in SIMPLE:
        return None, f"{stripped!r}: {low} doesn't exist on 1.12"
    if "C_" in args and low in ("/run", "/script"):
        return line, f"{stripped!r}: calls an Era-only C_ API"
    if client and low not in client.slash:
        return line, None  # probably an emote (/dance, /cheer...), which the slash list doesn't hold
    return line, None


def macro_spell(body):
    """The spell a macro casts (for its icon): #showtooltip's argument or the first /cast's spell."""
    for line in body.splitlines():
        line = line.strip()
        m = re.match(r"#show(?:tooltip)?\s+(.+)", line, re.I) or re.match(r"/cast\s+(.+)", line, re.I)
        if m:
            spell = re.sub(r"\[[^\]]*\]", "", m.group(1)).split(";")[0].strip()
            return re.sub(r"\s*\(Rank \d+\)$", "", spell)
    return None


def convert_macro(macro, client, era):
    """{'name', 'icon', 'body'} from a snapshot -> ({'name', 'icon', 'body'}, [notes])."""
    notes, lines = [], []
    for line in (macro.get("body") or "").splitlines():
        out, note = convert_line(line, client)
        if note:
            notes.append(note)
        if out is not None:
            lines.append(out)
    body = "\n".join(lines)
    if len(body) > MAX_BODY:
        notes.append(f"{len(body)} characters after conversion; 1.12 keeps the first {MAX_BODY}")
    icon = macro.get("icon")
    if icon == QUESTION_MARK or (isinstance(icon, str) and icon.lower() == "inv_misc_questionmark"):
        spell = macro_spell(macro.get("body") or "")
        icon = era.spell_icon.get(spell.lower(), QUESTION_MARK) if spell else QUESTION_MARK
    name = era.files.get(icon) if isinstance(icon, int) else str(icon)
    canonical = client.icons.get((name or "").lower()) if client else name
    if not canonical:
        if name:
            notes.append(f"icon {name} isn't in the 1.12 client: question mark")
        canonical = "INV_Misc_QuestionMark"
    return {"name": (macro.get("name") or " ")[:63].replace('"', "'"), "icon": canonical, "body": body}, notes


def macro_file(macros):
    """[(id, macro)] -> 1.12 macros-cache.txt."""
    out = []
    for mid, m in macros:
        out.append(f'MACRO {mid} "{m["name"]}" {m["icon"]}')
        out += [line for line in m["body"].splitlines()]
        out.append("END")
    return "".join(line + "\r\n" for line in out)


def account_macro_ids(db):
    """Which Era account macros (1-based index into accountMacros.list) become 1.12 account macros 1..18:
    those on any character's action bars first, then the rest in Era's order."""
    names = [m.get("name") for m in seq((db.get("accountMacros") or {}).get("list"))]
    used = {}
    for _, chars in entries(db.get("chars")):
        for _, snap in entries(chars):
            for _, a in entries((snap or {}).get("actions")):
                if a and a.get("t") == "macro" and isinstance(a.get("id"), int) and 1 <= a["id"] <= len(names):
                    if names[a["id"] - 1] == a.get("macro"):
                        used[a["id"]] = used.get(a["id"], 0) + 1
    order = sorted(used, key=lambda i: (-used[i], i)) + [i for i in range(1, len(names) + 1) if i not in used]
    keep = sorted(order[:MAX_MACROS])
    return {era: n for n, era in enumerate(keep, 1)}, [names[i - 1] for i in order[MAX_MACROS:]]


def account_macros(db, client, era):
    """(macros-cache.txt for the account, [notes])."""
    ids, dropped = account_macro_ids(db)
    macros = seq((db.get("accountMacros") or {}).get("list"))
    out, notes = [], []
    for era_index, mid in sorted(ids.items(), key=lambda t: t[1]):
        m, n = convert_macro(macros[era_index - 1], client, era)
        out.append((mid, m))
        notes += [f"account macro {m['name']!r}: {x}" for x in n]
    if dropped:
        notes.append(f"1.12 holds {MAX_MACROS} account macros; left out (not on any action bar): "
                     + ", ".join(repr(d) for d in dropped))
    return macro_file(out), notes


def character_macros(snap, client, era):
    """(macros-cache.txt for one character, {era action id: (type, action)}, [notes])."""
    out, notes = [], []
    for i, macro in enumerate(seq(snap.get("macros"))[:MAX_MACROS]):
        m, n = convert_macro(macro, client, era)
        out.append((CHARACTER_ID_BASE + i + 1, m))
        notes += [f"macro {m['name']!r}: {x}" for x in n]
    return macro_file(out), notes


def macro_action(a, snap, db):
    """An Era macro action slot -> (character_action.type, action), or None."""
    era_id, name = a.get("id"), a.get("macro")
    char_macros = seq(snap.get("macros"))
    account = seq((db.get("accountMacros") or {}).get("list"))
    if isinstance(era_id, int) and era_id > 120 and era_id - 121 < len(char_macros) \
            and char_macros[era_id - 121].get("name") == name and era_id - 121 < MAX_MACROS:
        return ACTION_CMACRO, era_id - 120
    ids, _ = account_macro_ids(db)
    if isinstance(era_id, int) and era_id in ids and account[era_id - 1].get("name") == name:
        return ACTION_MACRO, ids[era_id]
    for i, m in enumerate(char_macros[:MAX_MACROS]):  # the list moved since the scan: find it by name
        if m.get("name") == name:
            return ACTION_CMACRO, i + 1
    for era_index, mid in ids.items():
        if account[era_index - 1].get("name") == name:
            return ACTION_MACRO, mid
    return None


# ---------------------------------------------------------------------------------------------- bindings

def convert_bindings(era_text, client):
    """Era's bindings-cache.wtf (changes from Era's defaults) -> (1.12 bindings-cache.wtf, [notes])."""
    table = dict(client.default_bindings)  # key -> command, in the defaults' order
    notes, skipped = [], []
    for line in era_text.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0].lower() != "bind":
            continue
        key, command = parts[1], parts[2]
        table.pop(key, None)
        if command.upper() == "NONE":
            continue
        if command not in client.commands:
            skipped.append(f"{key} {command}")
            continue
        table[key] = command
    if skipped:
        notes.append("key bindings with no 1.12 command, left out: " + ", ".join(skipped))
    return "".join(f"bind {k} {c}\r\n" for k, c in table.items()), notes
