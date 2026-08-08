#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
TMP="/tmp/run_treb_laptop_supervised_v12_inner.sh"

cp "$BASE/run_treb_laptop_supervised.sh" "$TMP"
python - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
old='bash "$BASE/run_treb_laptop.sh" 2>&1 | tee "$LOG"'
new='bash "$BASE/run_treb_laptop_v12_fast.sh" 2>&1 | tee "$LOG"'
if old not in s:
    raise SystemExit('Could not locate supervised runner command')
p.write_text(s.replace(old,new,1))
PY
chmod +x "$TMP"
exec bash "$TMP"
