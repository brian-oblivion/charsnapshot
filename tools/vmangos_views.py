#!/usr/bin/env python3
"""Create v_<table> views in vmangos.sqlite that keep only the rows live at patch 1.12.1.

vmangos versions rows four ways:
  patch            one row per key per patch it changed in (0 = 1.2 … 10 = 1.12) → latest patch <= 10
  patch_min/max    row valid over a patch range                                → range contains 10
  build            like patch, by client build (5875 = 1.12.1)                 → latest build <= 5875
  build_min/max    like patch_min/max, by build                                → range contains 5875
"""
import sqlite3
import sys

PATCH, BUILD = 10, 5875

db = sqlite3.connect(sys.argv[1])
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
made = 0
for t in tables:
    info = db.execute(f'PRAGMA table_info("{t}")').fetchall()
    cols = {c[1] for c in info}
    pk = [c[1] for c in sorted(info, key=lambda c: c[5]) if c[5] > 0
          and c[1] not in ("patch", "build", "patch_min", "patch_max", "build_min", "build_max")]
    if {"patch_min", "patch_max"} <= cols:
        where = f"patch_min <= {PATCH} AND patch_max >= {PATCH}"
    elif {"build_min", "build_max"} <= cols:
        where = f"build_min <= {BUILD} AND build_max >= {BUILD}"
    elif "patch_min" in cols:
        where = f"patch_min <= {PATCH}"
    elif ("patch" in cols or "build" in cols) and pk:
        col, cap = ("patch", PATCH) if "patch" in cols else ("build", BUILD)
        same = " AND ".join(f'x."{k}" = t."{k}"' for k in pk)
        where = (f"{col} = (SELECT max(x.{col}) FROM \"{t}\" x WHERE {same} AND x.{col} <= {cap})")
    else:
        continue
    db.execute(f'DROP VIEW IF EXISTS "v_{t}"')
    db.execute(f'CREATE VIEW "v_{t}" AS SELECT * FROM "{t}" t WHERE {where}')
    made += 1
db.commit()
print(f"created {made} v_* views (patch 1.12 / build {BUILD})")
