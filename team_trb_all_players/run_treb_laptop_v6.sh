#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
INNER="/tmp/run_treb_laptop_v6_inner.sh"

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
# V6: preserve every validated RealGM season even when another historical page
# is unavailable. This supplementary source cannot block the strict Stage-1 QA.
sed -i '/python "$BASE\/normalize_roster_transactions.py"$/a python "$BASE/repair_normalized_transactions.py"\npython "$BASE/enrich_transactions_realgm_resilient_v3.py"\ncheckpoint "transaction_enrichment_realgm_partial" "$P"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/split_multi_stint_tenures.py"$/a python "$BASE/reconcile_roster_window_confidence.py"' /tmp/run_stage1_laptop.sh
# Persist a compact overlap distribution before the unchanged zero-overlap assertion.
sed -i '/python "$BASE\/audit_roster_tenure_consistency.py"$/a python "$BASE/summarize_tenure_overlaps.py"\ncheckpoint "tenure_overlap_diagnostic" "$P"' /tmp/run_stage1_laptop.sh
'''
s=s.replace(needle, insert, 1)
p.write_text(s)
PY
chmod +x "$INNER"
exec bash "$INNER"
