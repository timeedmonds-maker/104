#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
TMP="/tmp/run_treb_laptop_v13_inner.sh"

# Reuse the durable v12 fast orchestration, but replace the targeted source
# recovery with the full-description-cell parser, add the source-verified
# supplement for the handful of transaction-feed gaps, and use the runtime-safe
# boundary repair. This does not rerun the 780-team-season core or refetch the
# historical archive/schedule cache.
cp "$BASE/run_treb_laptop_v12_fast.sh" "$TMP"
python - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
s=s.replace('recover_overlap_events_from_bref_v12.py','recover_overlap_events_from_bref_v13.py')
s=s.replace('repair_overlap_boundaries_v12.py','repair_overlap_boundaries_v12_runtime.py')
needle='python "$BASE/recover_overlap_events_from_bref_v13.py"\npython "$BASE/repair_overlap_boundaries_v12_runtime.py"'
replacement='python "$BASE/recover_overlap_events_from_bref_v13.py"\npython "$BASE/apply_verified_overlap_supplement_v13.py"\npython "$BASE/repair_overlap_boundaries_v12_runtime.py"'
if needle not in s:
    raise SystemExit('Could not locate v13 source-recovery insertion point')
s=s.replace(needle,replacement,1)
selftest='python "$BASE/recover_overlap_events_from_bref_v13.py" --self-test\n'
if selftest not in s:
    raise SystemExit('Could not locate v13 self-test insertion point')
s=s.replace(selftest,selftest+'python "$BASE/apply_verified_overlap_supplement_v13.py" --self-test\n',1)
s=s.replace('TREB V12 FAST RECOVERY','TREB V13 FAST RECOVERY')
s=s.replace('V12 STRICT OVERLAPS=','V13 STRICT OVERLAPS=')
s=s.replace('V12 STAGE 1 EXACT-READY QA PASSED','V13 STAGE 1 EXACT-READY QA PASSED')
s=s.replace('V12_STAGE1_EXACT_READY=1','V13_STAGE1_EXACT_READY=1')
p.write_text(s)
PY
chmod +x "$TMP"
exec bash "$TMP"
