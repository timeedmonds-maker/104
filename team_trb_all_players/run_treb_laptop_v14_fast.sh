#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
TMP="/tmp/run_treb_laptop_v14_inner.sh"

# v14 reuses the fully cached v13 Stage1 orchestration and changes only the
# source-verified supplement. No 780-team-season core rerun and no historical
# archive/schedule refetch.
cp "$BASE/run_treb_laptop_v13_fast.sh" "$TMP"
python - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
s=s.replace('apply_verified_overlap_supplement_v13.py','apply_verified_overlap_supplement_v14.py')
s=s.replace('TREB V13 FAST RECOVERY','TREB V14 FAST RECOVERY')
s=s.replace('V13 STRICT OVERLAPS=','V14 STRICT OVERLAPS=')
s=s.replace('V13 STAGE 1 EXACT-READY QA PASSED','V14 STAGE 1 EXACT-READY QA PASSED')
s=s.replace('V13_STAGE1_EXACT_READY=1','V14_STAGE1_EXACT_READY=1')
s=s.replace('V13_STAGE1_VALIDATION_COMPLETE=1','V14_STAGE1_VALIDATION_COMPLETE=1')
p.write_text(s)
PY
chmod +x "$TMP"
exec bash "$TMP"
