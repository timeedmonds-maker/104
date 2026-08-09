#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
EXPECTED_BRANCH="treb-stage2-v7-base"
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
  'branch': 'treb-stage2-v7-base',
  'workers': int(os.environ['WORKERS_NOW']),
  'request_interval_seconds': float(os.environ['INTERVAL_NOW']),
  'batch_size': int(os.environ['BATCH_NOW']),
  'mode': 'Codespace Stage2 v7 player-scoped TEAM profiles with direct MinutesOn/MinutesOff extraction when validated; STAT request only as exactness-preserving fallback; core 780/780 reused; teammate pairs excluded'
})
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

# Hard preflight: prove the proposed one-request optimization using already-completed
# Stage2 cache files only. This makes ZERO network calls. If the TEAM metric rows do
# not contain a consistent minute pair matching the already-accepted STAT minutes,
# v7 stops before touching PBP Stats.
write_status "validating" "Running zero-network v7 cache validation before any API collection"
python "$BASE/probe_team_payload_minutes_from_cache.py" > /tmp/treb_v7_probe.json
cat /tmp/treb_v7_probe.json
python - <<'PY'
import json
p='/tmp/treb_v7_probe.json'
d=json.load(open(p))
assert d['network_calls'] == 0
assert d['files_with_metric_minutes'] > 0, d
assert d['mismatches'] == 0, d
assert d['ambiguous_metric_minute_pairs'] == 0, d
# Require broad evidence, not a one-off coincidence: at least 95% of completed
# player-scoped caches checked must expose the matching minute pair.
assert d['coverage_pct'] >= 95.0, d
print('V7 ZERO-NETWORK VALIDATION PASSED')
PY

python "$BASE/run_corrected_off_batch_v7.py" --self-test
write_status "validated" "v7 zero-network cache validation passed; starting player-scoped TEAM collection with direct minute extraction"
commit_progress "Validate Stage2 v7 one-request route"

round=0
no_progress_rounds=0
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

  write_status "collect" "round=$round complete=$complete remaining=$remaining v7 batch=$BATCH_SIZE workers=$WORKERS interval=$REQUEST_INTERVAL"
  python "$BASE/run_corrected_off_batch_v7.py" --batch-size "$BATCH_SIZE" --workers "$WORKERS" --request-interval "$REQUEST_INTERVAL" || true

  read -r new_complete new_remaining new_all_complete mode successes calls transient next_workers next_interval elapsed < <(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
c=d.get('network_counters') if isinstance(d.get('network_counters'),dict) else {}
print(
  int(d.get('complete_windows') or 0),
  int(d.get('remaining_windows') or 0),
  'true' if d.get('all_complete') else 'false',
  str(d.get('selection_mode') or 'unknown'),
  int(d.get('batch_successes') or 0),
  int(c.get('network_requests') or 0),
  float(d.get('transient_http_failure_rate') or 0.0),
  int(d.get('recommended_workers') or 1),
  float(d.get('recommended_request_interval_seconds') or 0.5),
  float(d.get('batch_elapsed_seconds') or 0.0),
)
PY
  )

  write_status "checkpoint" "round=$round complete=$new_complete remaining=$new_remaining mode=$mode successes=$successes network_calls=$calls transient_rate=$transient elapsed=${elapsed}s next_workers=$next_workers next_interval=$next_interval"
  commit_progress "Codespace v7 corrected OFF checkpoint round $round"

  WORKERS="$next_workers"
  REQUEST_INTERVAL="$next_interval"

  if [[ "$new_all_complete" == "true" ]]; then break; fi
  if (( new_complete > complete )); then
    no_progress_rounds=0
  else
    no_progress_rounds=$((no_progress_rounds+1))
  fi
  if (( no_progress_rounds >= 6 )); then
    write_status "stalled" "v7 made no durable progress for six rounds; stopping safely with all caches preserved"
    commit_progress "Stage2 v7 stalled safely"
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
write_status "complete" "Final corrected-OFF package exists and finalizer QA passed"
commit_progress "Complete TREB corrected OFF package from v7 Codespace"
echo "TREB V7 CODESPACE BUILD COMPLETE: $CORRECTED/TREB_corrected_off_final.zip"
