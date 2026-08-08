#!/usr/bin/env bash
set -euo pipefail

# One-touch, resumable Codespaces execution path for TREB Stage 1 -> corrected OFF -> final export.
# This exists specifically so the user never has to drive thousands of tenure records manually.
# It reuses the audited Stage 1 fallback and Stage 2 batch collector; no core/on-court rebuild and
# no teammate-pair analysis are performed.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
ROSTER="$DB/roster_tenure"
CORRECTED="$DB/corrected_off"
LOG="$DB/treb_codespace_end_to_end.log"
STATUS="$DB/treb_codespace_status.json"
LOCK="$DB/.treb_codespace_end_to_end.lock"
BATCH_SIZE="${TREB_BATCH_SIZE:-500}"
WORKERS="${TREB_WORKERS:-4}"
CHECKPOINT_EVERY="${TREB_CHECKPOINT_EVERY:-1}"

mkdir -p "$DB" "$CORRECTED"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another TREB Codespaces build is already running. Exiting without duplication."
  exit 0
fi

exec > >(tee -a "$LOG") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
status() {
  local phase="$1" detail="${2:-}"
  python - "$STATUS" "$phase" "$detail" <<'PY'
import json,sys,datetime
p,phase,detail=sys.argv[1:4]
d={"updated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"phase":phase,"detail":detail}
open(p,'w',encoding='utf-8').write(json.dumps(d,indent=2))
PY
}
checkpoint() {
  local message="$1"
  git config user.name "treb-codespace"
  git config user.email "treb-codespace@users.noreply.github.com"
  git add "$CORRECTED" "$STATUS" 2>/dev/null || true
  if git diff --cached --quiet; then
    echo "No new Stage 2 checkpoint changes"
    return 0
  fi
  git commit -m "$message [skip ci]"
  git push origin "HEAD:$BRANCH"
}

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

status "starting" "Installing/validating dependencies"
echo "[$(ts)] TREB one-touch Codespaces build starting on $BRANCH"
python -m pip install --upgrade requests beautifulsoup4 pandas numpy pyarrow duckdb

# Avoid a later GitHub-hosted runner waking up and duplicating the local build.
# Cancellation is best-effort; failure here does not weaken data QA.
if command -v gh >/dev/null 2>&1; then
  for wf in treb-stage1-historical.yml treb-corrected-off.yml treb-runner-probe.yml; do
    gh run list --workflow "$wf" --branch "$BRANCH" --limit 50 --json databaseId,status \
      --jq '.[] | select(.status=="queued" or .status=="waiting" or .status=="pending") | .databaseId' 2>/dev/null \
      | while read -r run_id; do
          [[ -n "$run_id" ]] || continue
          echo "Cancelling stale queued workflow $wf run $run_id before local execution"
          gh run cancel "$run_id" 2>/dev/null || true
        done
  done
fi

# Stage 1 is expensive enough that a clean exact-ready checkpoint should be reused on resume.
READY="false"
if [[ -s "$ROSTER/tenure_review_queue_summary.json" ]]; then
  READY="$(python - "$ROSTER/tenure_review_queue_summary.json" <<'PY'
import json,sys
q=json.load(open(sys.argv[1]))
print('true' if q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0 else 'false')
PY
)"
fi

if [[ "$READY" != "true" ]]; then
  status "stage1" "Building and auditing exact roster-tenure windows"
  echo "[$(ts)] Stage 1 exact-ready checkpoint absent; running full audited Stage 1 fallback"
  bash "$BASE/run_stage1_manual_fallback.sh"
fi

READY="$(python - "$ROSTER/tenure_review_queue_summary.json" <<'PY'
import json,sys
q=json.load(open(sys.argv[1]))
print('true' if q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0 else 'false')
PY
)"
if [[ "$READY" != "true" ]]; then
  status "stage1_review_required" "Stage 1 correctly stopped because exact-ready gate is not clean"
  echo "Stage 1 is not exact-ready. Corrected OFF has NOT started. The audit/review queue must be resolved first."
  exit 3
fi

# Stage 1's fallback may have dispatched a hosted corrected-OFF workflow. Cancel any queued copy
# before local Stage 2 so the two execution paths cannot race each other.
if command -v gh >/dev/null 2>&1; then
  gh run list --workflow treb-corrected-off.yml --branch "$BRANCH" --limit 50 --json databaseId,status \
    --jq '.[] | select(.status=="queued" or .status=="waiting" or .status=="pending") | .databaseId' 2>/dev/null \
    | while read -r run_id; do
        [[ -n "$run_id" ]] || continue
        echo "Cancelling queued hosted corrected-OFF run $run_id; local resumable Stage 2 owns execution"
        gh run cancel "$run_id" 2>/dev/null || true
      done
fi

status "stage2_selftest" "Validating corrected-OFF collector and finalizer"
python "$BASE/build_corrected_tenure_off.py" --self-test
python "$BASE/run_corrected_off_batch.py" --self-test
python "$BASE/finalize_corrected_off_package.py" --self-test

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
  if [[ "$COMPLETE" == "true" ]]; then
    break
  fi

  batch_no=$((batch_no+1))
  status "stage2_collect" "batch=$batch_no remaining=$REMAINING workers=$current_workers"
  echo "[$(ts)] Corrected OFF batch $batch_no: remaining=$REMAINING size=$BATCH_SIZE workers=$current_workers"

  set +e
  python "$BASE/run_corrected_off_batch.py" --batch-size "$BATCH_SIZE" --workers "$current_workers"
  rc=$?
  set -e

  if [[ $rc -ne 0 ]]; then
    consecutive_failures=$((consecutive_failures+1))
    echo "Batch attempt failed (attempt streak=$consecutive_failures). Successful window cache remains durable."
    checkpoint "Checkpoint TREB corrected OFF after partial batch"
    if [[ $consecutive_failures -ge 4 ]]; then
      status "stage2_blocked" "Repeated corrected-OFF batch failures; cache preserved"
      echo "Stopping after repeated failures so methodology is not weakened. Resume is safe from the existing cache."
      exit 4
    fi
    if [[ "$current_workers" -gt 1 ]]; then
      current_workers=$((current_workers/2))
      [[ "$current_workers" -ge 1 ]] || current_workers=1
      echo "Reducing workers to $current_workers for retry"
    fi
    sleep $((20 * consecutive_failures))
    continue
  fi

  consecutive_failures=0
  current_workers="$WORKERS"
  if (( batch_no % CHECKPOINT_EVERY == 0 )); then
    checkpoint "Advance TREB corrected OFF local batch $batch_no"
  fi
done

# Compatibility guard: the resumable batch summary historically used impact_windows_total while
# the finalizer initially expected impact_windows_requested. Preserve both names before final QA.
python - "$CORRECTED/corrected_off_collection_summary.json" <<'PY'
import json,sys
p=sys.argv[1]
s=json.load(open(p))
if 'impact_windows_requested' not in s and 'impact_windows_total' in s:
    s['impact_windows_requested']=s['impact_windows_total']
open(p,'w',encoding='utf-8').write(json.dumps(s,indent=2))
PY

status "finalizing" "Building DuckDB/Parquet/CSV/README/audit/ZIP package"
echo "[$(ts)] All corrected-OFF tenure windows complete; building final verified package"
python "$BASE/finalize_corrected_off_package.py"

status "complete" "TREB corrected-OFF historical database built and verified"
checkpoint "Complete TREB corrected OFF historical database"

echo "[$(ts)] TREB_END_TO_END_COMPLETE=1"
echo "Final ZIP: $CORRECTED/TREB_corrected_off_final.zip"
