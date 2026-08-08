#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
ROSTER="$DB/roster_tenure"
CORRECTED="$DB/corrected_off"
LOG="$DB/treb_local_unattended.log"
STATUS="$DB/treb_local_unattended_status.json"
LOCK="$DB/.treb_local_unattended.lock"
BATCH_SIZE="${TREB_BATCH_SIZE:-500}"
WORKERS="${TREB_WORKERS:-4}"
HEARTBEAT_SECONDS="${TREB_HEARTBEAT_SECONDS:-240}"
HEARTBEAT_PID=""

mkdir -p "$DB" "$CORRECTED"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another unattended TREB build already owns this checkout; duplicate launch skipped."
  exit 0
fi

exec > >(tee -a "$LOG") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
status() {
  python - "$STATUS" "$1" "${2:-}" <<'PY'
import json,sys,datetime
p,phase,detail=sys.argv[1:4]
open(p,'w',encoding='utf-8').write(json.dumps({
  'updated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'phase':phase,'detail':detail
},indent=2))
PY
}
heartbeat() {
  while true; do
    sleep "$HEARTBEAT_SECONDS"
    echo "[$(ts)] TREB_HEARTBEAT=1"
  done
}
cleanup() {
  [[ -n "${HEARTBEAT_PID:-}" ]] && kill "$HEARTBEAT_PID" 2>/dev/null || true
}
trap cleanup EXIT

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

git config user.name "treb-local-unattended"
git config user.email "treb-local-unattended@users.noreply.github.com"
heartbeat & HEARTBEAT_PID=$!
status "stage1" "Resuming exact roster-tenure build locally; hosted Actions queue bypassed"
echo "[$(ts)] TREB unattended local continuation started"

# Stage 1 is fully cached/resumable and deliberately does not touch completed core/on-court data.
set +e
bash "$BASE/run_stage1_local_resilient.sh"
STAGE1_RC=$?
set -e
if [[ $STAGE1_RC -ne 0 ]]; then
  if [[ $STAGE1_RC -eq 3 ]]; then
    status "stage1_review_required" "Stage 1 completed processing but exact-ready review gate is not clean"
  else
    status "stage1_blocked" "Stage 1 stopped with rc=$STAGE1_RC; log preserves exact failure"
  fi
  echo "STAGE1_STOPPED_RC=$STAGE1_RC"
  echo "Log: $LOG"
  exit "$STAGE1_RC"
fi

READY="$(python - "$ROSTER/tenure_review_queue_summary.json" <<'PY'
import json,sys
q=json.load(open(sys.argv[1]))
print('true' if q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0 else 'false')
PY
)"
if [[ "$READY" != "true" ]]; then
  status "stage1_review_required" "Exact-ready gate not clean; corrected OFF intentionally not started"
  exit 3
fi

status "stage2_selftest" "Validating corrected-OFF collector and finalizer"
python "$BASE/build_corrected_tenure_off.py" --self-test
python "$BASE/run_corrected_off_batch.py" --self-test
python "$BASE/finalize_corrected_off_package.py" --self-test

checkpoint() {
  local msg="$1"
  git add "$CORRECTED" "$STATUS" 2>/dev/null || true
  if git diff --cached --quiet; then return 0; fi
  git commit -m "$msg [skip ci]"
  for attempt in 1 2 3; do
    if git push origin "HEAD:$BRANCH"; then return 0; fi
    sleep $((10*attempt))
  done
  echo "WARNING: checkpoint committed locally but push failed; later checkpoint will retry"
}

batch_no=0
consecutive_failures=0
current_workers="$WORKERS"
while true; do
  COMPLETE="false"
  REMAINING="unknown"
  if [[ -s "$CORRECTED/corrected_off_collection_summary.json" ]]; then
    read -r COMPLETE REMAINING < <(python - "$CORRECTED/corrected_off_collection_summary.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))
print('true' if s.get('all_complete') else 'false', int(s.get('remaining_windows') or 0))
PY
)
  fi
  [[ "$COMPLETE" == "true" ]] && break

  batch_no=$((batch_no+1))
  status "stage2_collect" "batch=$batch_no remaining=$REMAINING workers=$current_workers"
  echo "[$(ts)] Corrected OFF batch $batch_no remaining=$REMAINING size=$BATCH_SIZE workers=$current_workers"

  set +e
  python "$BASE/run_corrected_off_batch.py" --batch-size "$BATCH_SIZE" --workers "$current_workers"
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    consecutive_failures=$((consecutive_failures+1))
    checkpoint "Checkpoint TREB corrected OFF after partial batch"
    if [[ $consecutive_failures -ge 4 ]]; then
      status "stage2_blocked" "Repeated corrected-OFF batch failures; successful cache preserved"
      echo "STAGE2_STOPPED_AFTER_REPEATED_FAILURES=1"
      exit 4
    fi
    if [[ "$current_workers" -gt 1 ]]; then
      current_workers=$((current_workers/2))
      [[ "$current_workers" -ge 1 ]] || current_workers=1
    fi
    sleep $((20*consecutive_failures))
    continue
  fi

  consecutive_failures=0
  current_workers="$WORKERS"
  checkpoint "Advance TREB corrected OFF local batch $batch_no"
done

python - "$CORRECTED/corrected_off_collection_summary.json" <<'PY'
import json,sys
p=sys.argv[1]
s=json.load(open(p))
if 'impact_windows_requested' not in s and 'impact_windows_total' in s:
    s['impact_windows_requested']=s['impact_windows_total']
open(p,'w',encoding='utf-8').write(json.dumps(s,indent=2))
PY

status "finalizing" "Building final verified corrected-OFF package"
checkpoint "Checkpoint TREB before final export"
python "$BASE/finalize_corrected_off_package.py"
status "complete" "TREB corrected-OFF historical database built and verified"
checkpoint "Complete TREB corrected OFF historical database"

echo "[$(ts)] TREB_END_TO_END_COMPLETE=1"
echo "Final ZIP: $CORRECTED/TREB_corrected_off_final.zip"
