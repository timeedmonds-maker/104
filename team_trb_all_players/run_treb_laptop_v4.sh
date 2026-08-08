#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
INNER="/tmp/run_treb_laptop_v4_inner.sh"

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
# V4 historical roster repair. RealGM blocks direct requests from some cloud
# clients, so use the resilient transport with Jina Reader and Wayback fallbacks.
# The source data remains the public RealGM transaction history and the strict
# zero-overlap QA gate is unchanged.
sed -i '/python "$BASE\/normalize_roster_transactions.py"$/a python "$BASE/repair_normalized_transactions.py"\npython "$BASE/enrich_transactions_realgm_resilient.py"\ncheckpoint "transaction_enrichment_realgm_resilient" "$P"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/split_multi_stint_tenures.py"$/a python "$BASE/reconcile_roster_window_confidence.py"' /tmp/run_stage1_laptop.sh
'''
s=s.replace(needle, insert, 1)
p.write_text(s)
PY
chmod +x "$INNER"
exec bash "$INNER"
