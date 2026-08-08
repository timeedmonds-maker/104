#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
INNER="/tmp/run_treb_laptop_v10_inner.sh"

python "$BASE/repair_historical_transactions_v4.py" --self-test
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
# V10 returns to the v8 modern-boundary logic (which produced the best strict
# overlap result) and attacks the surviving historical source defects instead.
# It fixes the pre-2002 Charlotte team id to match the NBA/core dataset, resolves
# historical name variants only under unique season/team context, and imports
# exact source-stated 10-day expiry rows from the cached Basketball-Reference
# archive. No QA assertion is weakened.
sed -i '/python "$BASE\/normalize_roster_transactions.py"$/a python "$BASE/repair_normalized_transactions.py"\npython "$BASE/replay_validated_realgm_cache.py"\npython "$BASE/repair_historical_transactions_v4.py"\ncheckpoint "transaction_enrichment_historical_v10" "$P"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/canonicalize_official_transaction_dates.py"$/a python "$BASE/repair_official_movement_boundaries.py"\ncheckpoint "official_movement_boundary_repair_v8" "$P"' /tmp/run_stage1_laptop.sh
sed -i 's#python "$BASE/split_multi_stint_tenures.py"#python "$BASE/split_multi_stint_tenures_v2.py"#' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/split_multi_stint_tenures_v2.py"$/a python "$BASE/reconcile_roster_window_confidence.py"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/audit_roster_tenure_consistency.py"$/a python "$BASE/summarize_tenure_overlaps.py"\ncheckpoint "tenure_overlap_diagnostic" "$P"' /tmp/run_stage1_laptop.sh
'''
s=s.replace(needle, insert, 1)
p.write_text(s)
PY
chmod +x "$INNER"
exec bash "$INNER"
