#!/usr/bin/env python3
"""Look up Classic Era Lua API functions, events and structures in Blizzard's own UI source.

  tools/era_api.py C_Container.GetContainerItemInfo     # signature, returns, struct fields, Era UI usage
  tools/era_api.py GetQuestLogTitle                     # legacy globals too: shows who calls it and how
  tools/era_api.py TAXIMAP_OPENED                       # events (by LiteralName) with payload
  tools/era_api.py -s taxi                              # search names and doc strings
  tools/era_api.py GetTalentInfo --usage 20             # more call sites
  tools/era_api.py C_TaxiMap.GetAllTaxiNodes --addons   # also grep the installed Era addons

Source: third_party/wow-ui-source or $WOW_UI_SOURCE (Gethe/wow-ui-source, branch classic_era), a mirror of the UI code the
Era client ships. Interface/AddOns/Blizzard_APIDocumentationGenerated/*.lua is Blizzard's generated API
documentation. Those docs are shared by every flavor (retail, Cata, Era...), so a documented function can
still be missing on Era. Evidence that it exists on Era: Era's own UI code calls it (the usage section),
or a maintained Era addon does. Functions defined in Blizzard_Deprecated*/ are Lua wrappers kept for old
addons; they only load while the CVar loadDeprecationFallbacks is on, so prefer the C_ API they wrap.

The doc files are parsed as data, never executed. The parsed docs are cached in data/era-api.json
(rebuilt when that checkout's commit changes). --addons greps $WOW_CLASSIC_ERA/Interface/AddOns.
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

import lua_data

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("WOW_UI_SOURCE", ROOT / "third_party" / "wow-ui-source")).expanduser()
ADDONS = SRC / "Interface" / "AddOns"
DOCS = ADDONS / "Blizzard_APIDocumentationGenerated"
CACHE = ROOT / "data" / "era-api.json"
WOW = Path(os.environ.get("WOW_CLASSIC_ERA", "_classic_era_")).expanduser()

def parse_doc(path):
    """A doc file is `local X = { ... }; APIDocumentation:AddDocumentationTable(X);`, parsed as data."""
    values = lua_data.loads(path.read_text(encoding="utf-8", errors="replace"))
    return next(iter(values.values()), None)


def head():
    return subprocess.run(["git", "-C", str(SRC), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def load():
    if not DOCS.is_dir():
        raise SystemExit(f"{SRC} is missing: git clone --depth 1 -b classic_era https://github.com/Gethe/wow-ui-source "
                         "third_party/wow-ui-source (or set WOW_UI_SOURCE)")
    commit = head()
    if CACHE.exists():
        cached = json.loads(CACHE.read_text())
        if cached.get("commit") == commit:
            return cached["docs"]
    docs = []
    for path in sorted(DOCS.glob("*.lua")):
        try:
            doc = parse_doc(path)
        except (IndexError, ValueError) as e:
            print(f"warning: could not parse {path.name}: {e!r}")
            continue
        if isinstance(doc, dict):
            docs.append(doc)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps({"commit": commit, "docs": docs}))
    return docs


def entries(docs):
    """Yield (kind, qualified name, entry, system) for every function, event and structure/enum."""
    for system in docs:
        ns = system.get("Namespace")
        for f in system.get("Functions") or []:
            yield "function", f"{ns}.{f['Name']}" if ns else f["Name"], f, system
        for e in system.get("Events") or []:
            yield "event", e.get("LiteralName", e["Name"]), e, system
        for t in system.get("Tables") or []:
            yield t.get("Type", "table").lower(), t["Name"], t, system


def fmt_field(f):
    typ = f.get("Type", "?")
    if f.get("InnerType"):
        typ += f"<{f['InnerType']}>"
    extra = []
    if f.get("Nilable"):
        extra.append("nilable")
    if "Default" in f:
        extra.append(f"default={f['Default']}")
    if f.get("Documentation"):
        extra.append(" ".join(f["Documentation"]))
    return f"{f['Name']}: {typ}" + (f"  ({'; '.join(extra)})" if extra else "")


def show(kind, name, e, system, structs):
    print(f"{name}  [{kind}, {system.get('Name')} system, Environment={system.get('Environment', '?')}]")
    for d in e.get("Documentation") or []:
        print(f"  {d}")
    for label, key in (("args", "Arguments"), ("returns", "Returns"), ("payload", "Payload"), ("fields", "Fields")):
        rows = e.get(key) or []
        if not rows:
            continue
        print(f"  {label}:")
        for f in rows:
            if "EnumValue" in f:
                print(f"    {f['Name']} = {f['EnumValue']}")
                continue
            print(f"    {fmt_field(f)}")
            inner = structs.get(f.get("InnerType") or f.get("Type"))
            if not (inner and inner.get("Fields") and key != "Fields"):
                continue
            if inner.get("Type") == "Enumeration":
                values = ", ".join(f"{v['Name']}={v.get('EnumValue')}" for v in inner["Fields"])
                print(f"      enum {values[:300]}{'…' if len(values) > 300 else ''}")
                continue
            for sub in inner["Fields"]:
                print(f"      .{fmt_field(sub)}")
    for flag in ("SecretArguments", "MayReturnNothing", "SynchronousEvent", "HasRestrictions"):
        if e.get(flag):
            print(f"  {flag}: {e[flag]}")


ERA_GAME_TYPES = {"vanilla", "classic", "standard"}  # Era's game type is vanilla; classic/standard include it
XML_INCLUDE = re.compile(r'<(?:Script|Include)\s+file="([^"]+)"', re.I)


def era_game_type_ok(spec):
    return not spec or bool(ERA_GAME_TYPES & {t.strip().lower() for t in spec.split(",")})


def era_files():
    """Files the Era client loads: each addon's _Vanilla.toc, else _Classic.toc, else plain .toc, honouring
    AllowLoadGameType. In TOC paths [Game] means Vanilla and [Family] means Classic on Era."""
    loaded = set()

    def add(path):
        if path in loaded or not path.is_file():
            return
        loaded.add(path)
        if path.suffix.lower() == ".xml":
            for inc in XML_INCLUDE.findall(path.read_text(encoding="utf-8", errors="replace")):
                add((path.parent / inc.replace("\\", "/")).resolve())

    for addon in sorted(p for p in ADDONS.iterdir() if p.is_dir()):
        candidates = (f"{addon.name}_Vanilla.toc", f"{addon.name}_Classic.toc", f"{addon.name}.toc")
        toc = next((addon / t for t in candidates if (addon / t).exists()), None)
        if not toc:
            continue
        lines = toc.read_text(encoding="utf-8", errors="replace").splitlines()
        meta = dict(re.match(r"##\s*([^:]+):\s*(.*)", l).groups() for l in lines if re.match(r"##\s*[^:]+:", l))
        if not era_game_type_ok(meta.get("AllowLoadGameType")) or meta.get("AllowLoad", "").lower() == "glue":
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cond = re.search(r"\[AllowLoadGameType ([^\]]+)\]", line)
            if cond and not era_game_type_ok(cond.group(1)):
                continue
            rel = line.split()[0].replace("[Game]", "Vanilla").replace("[Family]", "Classic").replace("\\", "/")
            add((addon / rel).resolve())
    return loaded


def rg(pattern, paths, extra=()):
    cmd = ["rg", "-n", "--no-heading", "-g", "*.lua", "-g", "*.xml", *extra, pattern, *map(str, paths)]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()


def usage(name, limit):
    bare = name.split(".")[-1]
    word = re.escape(name if "." in name else bare)
    deprecated = [p for p in ADDONS.glob("Blizzard_Deprecated*")]
    wrappers = rg(rf"(function\s+{re.escape(bare)}\s*\(|\b{re.escape(bare)}\s*=\s*function)", deprecated)
    if wrappers:
        print("  deprecated wrapper (only loads with CVar loadDeprecationFallbacks):")
        for line in wrappers[:6]:
            print("    " + line.replace(str(ADDONS) + "/", ""))
    hits = rg(rf"\b{word}\b", [ADDONS], ["-g", "!Blizzard_APIDocumentationGenerated/**", "-g", "!Blizzard_Deprecated*/**"])
    era = era_files()
    era_hits = [h for h in hits if (ADDONS / h.replace(str(ADDONS) + "/", "").partition(":")[0]).resolve() in era]
    print(f"  Era UI call sites: {len(era_hits)} (plus {len(hits) - len(era_hits)} in other flavors' files)"
          + ("  -> no Era evidence; check an Era addon before relying on it" if not era_hits else ""))
    for line in era_hits[:limit]:
        path, _, rest = line.replace(str(ADDONS) + "/", "").partition(":")
        lineno, _, code = rest.partition(":")
        print(f"    {path}:{lineno}  {code.strip()[:140]}")


def addon_usage(name, limit):
    """Call sites in the Era addons installed locally (third-party evidence that an API works on Era)."""
    installed = WOW / "Interface" / "AddOns"
    if not installed.is_dir():
        print(f"  installed addons: {installed} not found (set WOW_CLASSIC_ERA)")
        return
    word = re.escape(name if "." in name else name.split(".")[-1])
    hits = rg(rf"\b{word}\b", [installed], ["-g", "!**/Libs/**", "-g", "!**/libs/**"])
    by_addon = {}
    for h in hits:
        by_addon.setdefault(h.replace(str(installed) + "/", "").split("/")[0], []).append(h)
    print(f"  installed Era addons calling it: {', '.join(sorted(by_addon)) or 'none'}")
    for h in hits[:limit]:
        path, _, rest = h.replace(str(installed) + "/", "").partition(":")
        lineno, _, code = rest.partition(":")
        print(f"    {path}:{lineno}  {code.strip()[:140]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="function (C_X.Name or Name), event (EVENT_NAME), structure or enum")
    ap.add_argument("-s", "--search", action="store_true", help="substring search over names and docs")
    ap.add_argument("--usage", type=int, default=8, metavar="N", help="call sites to print (default 8)")
    ap.add_argument("--addons", action="store_true", help="also search the installed Era addons (WOW_CLASSIC_ERA)")
    args = ap.parse_args()

    docs = load()
    all_entries = list(entries(docs))
    structs = {n: e for k, n, e, _ in all_entries if k in ("structure", "enumeration", "constants")}
    q = args.name.lower()

    if args.search:
        for kind, name, e, system in all_entries:
            text = " ".join([name, *(e.get("Documentation") or [])]).lower()
            if q in text:
                print(f"{kind:12} {name}")
        return

    matches = [x for x in all_entries if x[1].lower() == q or x[1].split(".")[-1].lower() == q
               or (x[0] == "event" and x[2]["Name"].lower() == q)]
    if not matches:
        print(f"{args.name}: not in the generated docs (legacy global or not an API).")
    for kind, name, e, system in matches:
        show(kind, name, e, system, structs)
        print()
    name = matches[0][1] if len(matches) == 1 and matches[0][0] == "function" else args.name
    usage(name, args.usage)
    if args.addons:
        addon_usage(name, args.usage)


if __name__ == "__main__":
    main()
