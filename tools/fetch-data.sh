#!/usr/bin/env bash
# Download the reference data char_snapshot.py needs into data/ (gitignored).
#
#   data/era-client.sqlite   Classic Era client tables (DB2 -> CSV via wago.tools): talents, skill lines,
#                            skill-line abilities, random item properties, spell names and icons, interface file names
#   data/vmangos.sqlite      vmangos world DB (1.12 items, spells, quests, factions, spawns), with v_ views
#                            that keep only the rows live at patch 1.12.1
#
# Usage: tools/fetch-data.sh [era|vmangos|all]   (default: all). Needs curl, unzip, python3, sqlite3.
# Rerun `era` after a Classic Era patch to pick up the new client build.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
data="$root/data"
mkdir -p "$data"

DB2_TABLES=(Talent SkillLine SkillLineAbility ItemRandomProperties ManifestInterfaceData SpellName SpellMisc)

fetch_era() {
  local build csvdir db
  build=$(curl -fsS https://wago.tools/api/builds |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["wow_classic_era"][0]["version"])')
  csvdir="$data/era-client-csv/$build"
  db="$data/era-client.sqlite"
  echo "Classic Era client build $build"
  mkdir -p "$csvdir"
  for t in "${DB2_TABLES[@]}"; do
    [[ -s "$csvdir/$t.csv" ]] && continue
    echo "  $t"
    curl -fsS -o "$csvdir/$t.csv" "https://wago.tools/db2/$t/csv?build=$build"
  done
  rm -f "$db.tmp"
  python3 "$root/tools/csv_to_sqlite.py" "$db.tmp" $(printf "$csvdir/%s.csv " "${DB2_TABLES[@]}")
  sqlite3 "$db.tmp" "CREATE TABLE _meta(key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO _meta VALUES ('build', '$build'), ('source', 'wago.tools wow_classic_era'),
                             ('fetched', datetime('now'));"
  mv "$db.tmp" "$db"
  echo "-> $db"
}

fetch_vmangos() {
  local url tmp f
  url=$(curl -fsS https://api.github.com/repos/vmangos/core/releases/tags/db_latest |
    python3 -c 'import json,sys
for a in json.load(sys.stdin)["assets"]:
    if a["name"].startswith("db-sqlite-"): print(a["browser_download_url"])')
  echo "vmangos DB: $url"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' RETURN
  curl -fsSL -o "$tmp/db.zip" "$url"
  unzip -q "$tmp/db.zip" -d "$tmp/x"
  f=$(find "$tmp/x" -type f \( -name '*.sqlite' -o -name '*.db' -o -name '*.sqlite3' \) | head -1)
  [[ -n "$f" ]] || { echo "no sqlite file in archive:"; find "$tmp/x" -type f; exit 1; }
  mv "$f" "$data/vmangos.sqlite"
  python3 "$root/tools/vmangos_views.py" "$data/vmangos.sqlite"
  echo "$url" > "$data/vmangos.source"
  echo "-> $data/vmangos.sqlite"
}

case "${1:-all}" in
  era) fetch_era ;;
  vmangos) fetch_vmangos ;;
  all) fetch_era; fetch_vmangos ;;
  *) echo "usage: $0 [era|vmangos|all]" >&2; exit 2 ;;
esac
