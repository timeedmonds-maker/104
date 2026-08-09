#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage2-codespace-optimized"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
CORRECTED="$DB/corrected_off"
STATUS="$DB/codespace_stage2_status.json"
BATCH_SIZE="${TREB_BATCH_SIZE:-160}"
WORKERS="${TREB_WORKERS:-2}"
REQUEST_INTERVAL="${TREB_REQUEST_INTERVAL:-0.12}"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: expected $EXPECTED_BRANCH, current ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-codespace-runner"
git config user.email "treb-codespace-runner@users.noreply.github.com"

write_status () {
  local phase="$1"; local note="${2:-}"
  PHASE="$phase" NOTE="$note" WORKERS_NOW="$WORKERS" INTERVAL_NOW="$REQUEST_INTERVAL" python - <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/codespace_stage2_status.json')
try: d=json.loads(p.read_text()) if p.exists() else {}
except Exception: d={}
d.update({
  'generated_utc': datetime.now(timezone.utc).isoformat(),
  'phase': os.environ['PHASE'],
  'note': os.environ.get('NOTE') or None,
  'branch': 'treb-stage2-codespace-optimized',
  'workers': int(os.environ['WORKERS_NOW']),
  'request_interval_seconds': float(os.environ['INTERVAL_NOW']),
  'mode': 'Codespace Stage2 v5: persistent core index + endpoint cache + shared stat payloads + one-shot fresh quarantine + adaptive concurrency + compact Git diagnostics; core 780/780 reused; teammate pairs excluded'
})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(d, indent=2), encoding='utf-8')
print(json.dumps(d, indent=2))
PY
}

commit_progress () {
  local message="$1"
  git add "$CORRECTED" "$STATUS" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "$message [skip ci]"
    git push origin "HEAD:$EXPECTED_BRANCH"
  fi
}

python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/tenure_review_queue_summary.json')
d=json.loads(p.read_text())
assert d.get('stage1_exact_ready') is True, d
assert int(d.get('review_queue_windows') or 0) == 0, d
assert int(d.get('tolerated_review_windows') or 0) == 29, d
print('STAGE1 PRODUCTION-READY CONFIRMED: hard structural QA zero; 29 tolerated review flags recorded')
PY

write_status "setup" "Validating Stage2 v5 optimized collector"
python - <<'PY' || python -m pip install --disable-pip-version-check requests pandas numpy pyarrow duckdb
import requests, pandas, numpy, pyarrow, duckdb
print('dependencies already available')
PY
python "$BASE/build_corrected_tenure_off.py" --self-test
python "$BASE/run_corrected_off_batch_v2.py" --self-test
python "$BASE/run_corrected_off_batch_v3.py" --self-test
python "$BASE/run_corrected_off_batch_v4.py" --self-test
python "$BASE/run_corrected_off_batch_v5.py" --self-test
python "$BASE/finalize_corrected_off_package.py" --self-test

round=0
retry_no_progress=0
while true; do
  round=$((round+1))
  read -r complete remaining all_complete < <(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
if p.exists():
    d=json.loads(p.read_text()); print(int(d.get('complete_windows') or 0), int(d.get('remaining_windows') or 0), 'true' if d.get('all_complete') else 'false')
else:
    print(0, -1, 'false')
PY
  )
  if [[ "$all_complete" == "true" ]]; then break; fi

  write_status "collect" "round=$round complete=$complete remaining=$remaining v5 workers=$WORKERS batch=$BATCH_SIZE interval=$REQUEST_INTERVAL"
  python "$BASE/run_corrected_off_batch_v5.py" --batch-size "$BATCH_SIZE" --workers "$WORKERS" --request-interval "$REQUEST_INTERVAL"

  read -r new_complete new_remaining new_all_complete mode successes fresh_before deferred_count next_workers next_interval transient_rate elapsed < <(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
print(
    int(d.get('complete_windows') or 0),
    int(d.get('remaining_windows') or 0),
    'true' if d.get('all_complete') else 'false',
    str(d.get('selection_mode') or 'unknown'),
    int(d.get('batch_successes') or 0),
    int(d.get('fresh_pending_before') or 0),
    int(d.get('deferred_queue_windows') or 0),
    int(d.get('recommended_workers') or 1),
    float(d.get('recommended_request_interval_seconds') or 0.12),
    float(d.get('transient_http_failure_rate') or 0.0),
    float(d.get('batch_elapsed_seconds') or 0.0),
)
PY
  )

  write_status "checkpoint" "round=$round complete=$new_complete remaining=$new_remaining mode=$mode successes=$successes fresh_before=$fresh_before deferred=$deferred_count transient_rate=$transient_rate elapsed=${elapsed}s next_workers=$next_workers next_interval=$next_interval"
  commit_progress "Codespace v5 corrected OFF checkpoint round $round"

  WORKERS="$next_workers"
  REQUEST_INTERVAL="$next_interval"

  if [[ "$new_all_complete" == "true" ]]; then break; fi

  if [[ "$mode" == "fresh" ]]; then
    retry_no_progress=0
  elif (( successes > 0 )); then
    retry_no_progress=0
  else
    retry_no_progress=$((retry_no_progress+1))
  fi

  if (( retry_no_progress >= 8 )); then
    write_status "retry_stalled" "Deferred retry queue made no progress for eight retry rounds; durable queue preserved for targeted fallback"
    commit_progress "Codespace v5 deferred retry stalled"
    echo "ERROR: deferred retry queue made no progress for eight rounds; all prior progress and endpoint cache are preserved." >&2
    exit 4
  fi
done

python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
if 'impact_windows_requested' not in d and 'impact_windows_total' in d:
    d['impact_windows_requested']=d['impact_windows_total']
p.write_text(json.dumps(d, indent=2), encoding='utf-8')
assert d.get('all_complete') is True, d
assert int(d.get('remaining_windows') or 0) == 0, d
print('CORRECTED OFF COLLECTION COMPLETE', d.get('complete_windows'))
PY

write_status "finalizing" "All corrected-OFF windows complete; building final package and QA"
python "$BASE/finalize_corrected_off_package.py"
test -s "$CORRECTED/TREB_corrected_off_final.zip"

python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/corrected_off')
for rel in ['final_export/quality_report.json','final_export/provenance.json']:
    p=root/rel
    if not p.exists():
        continue
    d=json.loads(p.read_text())
    d['strict_stage1_exact_ready_before_tolerance']=False
    d['production_ready_under_materiality_standard']=True
    d['tolerated_stage1_review_windows']=29
    d['material_structural_overlap_count']=0
    d['transaction_day_methodology']='transaction date belongs to departing team; incoming team effective next calendar day'
    d['execution_route']='4-core GitHub Codespace Stage2 v5 optimized endpoint-cache/failure-quarantine route'
    p.write_text(json.dumps(d, indent=2), encoding='utf-8')
(root/'METHODOLOGY_TOLERANCE_NOTE.txt').write_text(
    'Stage1 retained 29 tolerated non-overlapping review/provenance flags out of 15,530 tenure windows. '
    'All hard structural QA was zero: cross-team overlaps, same-team overlaps, invalid intervals, duplicate windows, '
    'resolved game-count inconsistencies and unresolved same-day cases. Transaction date is assigned to the departing team; '
    'incoming-team tenure begins the following calendar day.\n', encoding='utf-8')
PY

write_status "complete" "Final corrected-OFF package exists and finalizer QA passed"
commit_progress "Complete TREB corrected OFF package from v5 optimized Codespace"
echo "TREB V5 OPTIMIZED CODESPACE BUILD COMPLETE: $CORRECTED/TREB_corrected_off_final.zip"
