#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
INNER="/tmp/run_treb_laptop_v3_inner.sh"

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
# V3 historical roster repair: the Basketball-Reference archive omits a material
# number of exact waiver / free-agent / 10-day-expiry boundaries. RealGM league
# transaction histories provide those dated events. Enrich before tenure build;
# keep the strict overlap QA unchanged.
sed -i '/python "$BASE\/normalize_roster_transactions.py"$/a python "$BASE/repair_normalized_transactions.py"\npython "$BASE/enrich_transactions_realgm.py"\ncheckpoint "transaction_enrichment_realgm" "$P"' /tmp/run_stage1_laptop.sh
sed -i '/python "$BASE\/split_multi_stint_tenures.py"$/a python "$BASE/reconcile_roster_window_confidence.py"' /tmp/run_stage1_laptop.sh
'''
s=s.replace(needle, insert, 1)
p.write_text(s)
PY
chmod +x "$INNER"
exec bash "$INNER"
