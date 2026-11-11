#!/usr/bin/env python3
"""Load CSVs into SQLite with numbers stored as numbers.

sqlite3's .import makes every column TEXT, so `SpellID = 16609` silently matches nothing and
ORDER BY / < > compare as strings. Here columns get INTEGER/REAL/TEXT affinity from their values.

Usage: csv_to_sqlite.py DB file.csv...   (table name = file stem)
"""
import csv
import os
import sqlite3
import sys


def kind(v):
    try:
        int(v)
        return 0
    except ValueError:
        pass
    try:
        float(v)
        return 1
    except ValueError:
        return 2


db = sqlite3.connect(sys.argv[1])
for path in sys.argv[2:]:
    table = os.path.splitext(os.path.basename(path))[0]
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, rows = rows[0], rows[1:]
    kinds = [max((kind(r[i]) for r in rows if r[i] != ""), default=2) for i in range(len(header))]
    affinity = ["INTEGER", "REAL", "TEXT"]
    cols = ", ".join(f'"{h}" {affinity[k]}' for h, k in zip(header, kinds))
    db.execute(f'DROP TABLE IF EXISTS "{table}"')
    db.execute(f'CREATE TABLE "{table}" ({cols})')
    db.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" * len(header))})', rows)
    if "ID" in header:
        db.execute(f'CREATE INDEX "{table}_ID" ON "{table}"("ID")')
    for c in header:
        if c == "SpellID" or c == "ItemID":
            db.execute(f'CREATE INDEX "{table}_{c}" ON "{table}"("{c}")')
db.commit()
