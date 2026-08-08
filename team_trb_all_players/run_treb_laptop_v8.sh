#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
INNER="/tmp/run_treb_laptop_v8_inner.sh"

# Cheap deterministic tests for the two new chronology repairs before touching
# any durable data.
python "$BASE/repair_official_movement_boundaries.py" --self-test
python - <<'PY'
import sys
sys.path.insert(0, 'team_trb_all_players')
import split_multi_stint_tenures_v2 as m
m.self_test()
PY

cp "$BASE/run_treb_laptop.sh" "$INNER"
python - "$INNER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
needle='chmod +x /tmp/run_stage1_laptop.sh\n'
if needle not in s:
    raise SystemExit('Could not locate Stage 1 temporary-runner hook')
insert = needle + r'''
# V8: no repeated RealGM network loop. Replay only the already-validated local
# RealGM caches, then use the official NBA movement feed as the modern source of
# truth. Repair authoritative trade source IDs, derive natural 10-day contract
# endpoints from the official signing description + cached schedule, and rebuild
# repeated/same-day transaction chronology without creating season-long phantom
# stints. The strict zero-overlap QA assertion remains unchanged.
sed -i '/python "$BASE\/normalize_roster_transactions.py"$/a python "$BASE/repair_normalized_transactions.py"\npython "$BASE/replay_validated_realgm_cache.py"\ncheckpoint "transaction_enrichment_cached_sources" "$P"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/canonicalize_official_transaction_dates.py"$/a python "$BASE/repair_official_movement_boundaries.py"\ncheckpoint "official_movement_boundary_repair" "$P"' /tmp/run_stage1_laptop.sh
sed -i 's#python "$BASE/split_multi_stint_tenures.py"#python "$BASE/split_multi_stint_tenures_v2.py"#' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/split_multi_stint_tenures_v2.py"$/a python "$BASE/reconcile_roster_window_confidence.py"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/audit_roster_tenure_consistency.py"$/a python "$BASE/summarize_tenure_overlaps.py"\ncheckpoint "tenure_overlap_diagnostic" "$P"' /tmp/run_stage1_laptop.sh
'''
s=s.replace(needle, insert, 1)
p.write_text(s)
PY
chmod +x "$INNER"
exec bash "$INNER"
