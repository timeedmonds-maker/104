#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
EXPECTED_BRANCH="treb-stage2-v8-base"
BRANCH="$(git branch --show-current)"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
CORRECTED="$DB/corrected_off"
STATUS="$DB/codespace_stage2_status.json"
BATCH_SIZE="${TREB_BATCH_SIZE:-200}"
WORKERS="${TREB_WORKERS:-1}"
REQUEST_INTERVAL="${TREB_REQUEST_INTERVAL:-0.50}"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: expected $EXPECTED_BRANCH, current ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-codespace-runner"
git config user.email "treb-codespace-runner@users.noreply.github.com"

write_status () {
  local phase="$1"; local note="${2:-}"
  PHASE="$phase" NOTE="$note" WORKERS_NOW="$WORKERS" INTERVAL_NOW="$REQUEST_INTERVAL" BATCH_NOW="$BATCH_SIZE" python - <<'PY'
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
  'branch': 'treb-stage2-v8-base',
  'workers': int(os.environ['WORKERS_NOW']),
  'request_interval_seconds': float(os.environ['INTERVAL_NOW']),
  'batch_size': int(os.environ['BATCH_NOW']),
  'mode': 'Codespace Stage2 v8: player-scoped TEAM metrics + documented date-filtered get-totals minutes; total team court time derived from sum player SecondsPlayed/5; endpoint payloads cached/shared; core 780/780 reused; teammate pairs excluded'
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
print('STAGE1 PRODUCTION-READY CONFIRMED')
PY

# Zero-network mathematical validation against the completed 780/780 core.
# This verifies across historical player/team seasons that:
#   MinutesOn = SecondsPlayed / 60
#   MinutesOff = sum(all team player SecondsPlayed) / 5 / 60 - MinutesOn
# including overtime. The same get-totals data structure is then requested with
# FromDate/ToDate for partial tenure intervals in production.
write_status "validating" "Running zero-network historical validation of totals-derived MinutesOn/MinutesOff"
set +e
python "$BASE/validate_totals_minutes_formula.py" > /tmp/treb_v8_formula_validation.json
VALIDATION_RC=$?
set -e
cat /tmp/treb_v8_formula_validation.json
if (( VALIDATION_RC != 0 )); then
  write_status "validation_failed" "v8 totals-derived minutes formula did not reproduce completed-core on/off minutes; zero production API calls made"
  git add "$STATUS" && git commit -m "Record Stage2 v8 validation failure [skip ci]" || true
  git push origin "HEAD:$EXPECTED_BRANCH" || true
  exit 6
fi

echo "V8 ZERO-NETWORK HISTORICAL VALIDATION PASSED"

# Independent code-level sanity check (the v8 production derivation, not its CLI test).
python - <<'PY'
import sys
sys.path.insert(0, 'team_trb_all_players')
import run_corrected_off_batch_v8 as v8
p={'multi_row_table_data':[
 {'EntityId':'1','Name':'A','SecondsPlayed':600},
 {'EntityId':'2','Name':'B','SecondsPlayed':600},
 {'EntityId':'3','Name':'C','SecondsPlayed':600},
 {'EntityId':'4','Name':'D','SecondsPlayed':600},
 {'EntityId':'5','Name':'E','SecondsPlayed':600},
]}
on,off,row,detail=v8.derive_minutes_from_totals(p,'1','A')
assert abs(on-10.0)<1e-9 and abs(off-40.0)<1e-9, (on,off,detail)
print('V8 DERIVATION SANITY CHECK PASSED')
PY

write_status "validated" "v8 formula reproduced completed core; starting small live canary with documented date-filtered get-totals endpoint"
commit_progress "Validate Stage2 v8 totals-derived minutes route"

# Canary first: no full-scale launch until the new endpoint combination produces
# durable corrected-OFF windows. Three 40-window passes max; all successes persist.
canary_success=0
for canary in 1 2 3; do
  before=$(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text()); print(int(d.get('complete_windows') or 0))
PY
)
  write_status "canary" "v8 live canary=$canary before=$before batch=40 workers=1 interval=$REQUEST_INTERVAL"
  python "$BASE/run_corrected_off_batch_v8.py" --batch-size 40 --workers 1 --request-interval "$REQUEST_INTERVAL" || true
  read -r after successes transient next_workers next_interval < <(python - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json').read_text())
print(int(d.get('complete_windows') or 0), int(d.get('batch_successes') or 0), float(d.get('transient_http_failure_rate') or 0), int(d.get('recommended_workers') or 1), float(d.get('recommended_request_interval_seconds') or 0.5))
PY
)
  write_status "canary_checkpoint" "v8 canary=$canary complete=$after added=$((after-before)) successes=$successes transient_rate=$transient"
  commit_progress "Codespace v8 canary $canary"
  WORKERS="$next_workers"
  REQUEST_INTERVAL="$next_interval"
  if (( after > before )); then
    canary_success=1
    break
  fi
done

if (( canary_success == 0 )); then
  write_status "canary_failed" "v8 made zero durable completions across three small canaries; stopping safely before full-scale collection"
  commit_progress "Stage2 v8 canary stopped safely"
  exit 7
fi

write_status "collect" "v8 canary produced durable completions; entering full fresh-first collection"
commit_progress "Stage2 v8 canary passed"

round=0
no_progress_rounds=0
while true; do
  round=$((round+1))
  read -r complete remaining all_complete < <(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text()); print(int(d.get('complete_windows') or 0), int(d.get('remaining_windows') or 0), 'true' if d.get('all_complete') else 'false')
PY
)
  if [[ "$all_complete" == "true" ]]; then break; fi

  write_status "collect" "round=$round complete=$complete remaining=$remaining v8 batch=$BATCH_SIZE workers=$WORKERS interval=$REQUEST_INTERVAL"
  python "$BASE/run_corrected_off_batch_v8.py" --batch-size "$BATCH_SIZE" --workers "$WORKERS" --request-interval "$REQUEST_INTERVAL" || true

  read -r new_complete new_remaining new_all_complete mode successes calls transient next_workers next_interval elapsed < <(python - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json').read_text())
c=d.get('network_counters') if isinstance(d.get('network_counters'),dict) else {}
print(int(d.get('complete_windows') or 0), int(d.get('remaining_windows') or 0), 'true' if d.get('all_complete') else 'false', str(d.get('selection_mode') or 'unknown'), int(d.get('batch_successes') or 0), int(c.get('network_requests') or 0), float(d.get('transient_http_failure_rate') or 0), int(d.get('recommended_workers') or 1), float(d.get('recommended_request_interval_seconds') or 0.5), float(d.get('batch_elapsed_seconds') or 0))
PY
)

  write_status "checkpoint" "round=$round complete=$new_complete remaining=$new_remaining mode=$mode successes=$successes network_calls=$calls transient_rate=$transient elapsed=${elapsed}s next_workers=$next_workers next_interval=$next_interval"
  commit_progress "Codespace v8 corrected OFF checkpoint round $round"
  WORKERS="$next_workers"
  REQUEST_INTERVAL="$next_interval"

  if [[ "$new_all_complete" == "true" ]]; then break; fi
  if (( new_complete > complete )); then no_progress_rounds=0; else no_progress_rounds=$((no_progress_rounds+1)); fi
  if (( no_progress_rounds >= 6 )); then
    write_status "stalled" "v8 made no durable progress for six rounds; stopping safely with all caches preserved"
    commit_progress "Stage2 v8 stalled safely"
    exit 4
  fi
done

python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
if 'impact_windows_requested' not in d and 'impact_windows_total' in d: d['impact_windows_requested']=d['impact_windows_total']
p.write_text(json.dumps(d, indent=2), encoding='utf-8')
assert d.get('all_complete') is True and int(d.get('remaining_windows') or 0)==0, d
print('CORRECTED OFF COLLECTION COMPLETE', d.get('complete_windows'))
PY

write_status "finalizing" "All corrected-OFF windows complete; building final package and QA"
python "$BASE/finalize_corrected_off_package.py"
test -s "$CORRECTED/TREB_corrected_off_final.zip"
write_status "complete" "Final corrected-OFF package exists and finalizer QA passed"
commit_progress "Complete TREB corrected OFF package from v8 Codespace"
echo "TREB V8 CODESPACE BUILD COMPLETE: $CORRECTED/TREB_corrected_off_final.zip"
