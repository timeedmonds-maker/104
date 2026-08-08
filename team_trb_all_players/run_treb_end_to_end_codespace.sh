#!/usr/bin/env bash
set -euo pipefail

# One-touch, resumable Codespaces execution path for TREB Stage 1 -> corrected OFF -> final export.
# No completed core/on-court rebuild. No teammate-pair analysis.

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
  echo "Another TREB Codespaces build already owns this checkout. Duplicate launcher exiting cleanly."
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
push_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if git push origin "HEAD:$BRANCH"; then
      return 0
    fi
    echo "Git push attempt $attempt failed; retrying shortly"
    sleep $((10 * attempt))
  done
  return 1
}
publish_status() {
  git add "$STATUS" 2>/dev/null || true
  if git diff --cached --quiet; then
    return 0
  fi
  git commit -m "TREB Codespace status checkpoint [skip ci]"
  push_with_retry || echo "WARNING: status checkpoint could not be pushed yet; local execution remains resumable"
}
checkpoint() {
  local message="$1"
  git add "$CORRECTED" "$STATUS" 2>/dev/null || true
  if git diff --cached --quiet; then
    echo "No new Stage 2 checkpoint changes"
    return 0
  fi
  git commit -m "$message [skip ci]"
  push_with_retry || echo "WARNING: checkpoint is committed locally but not yet pushed; a later checkpoint will retry"
}

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

git config user.name "treb-codespace"
git config user.email "treb-codespace@users.noreply.github.com"

# A fresh Codespace should start from the newest branch. On restart, preserve any local
# uncommitted cache rather than pulling over it.
if [[ -z "$(git status --porcelain)" ]]; then
  git pull --ff-only origin "$BRANCH"
else
  echo "Local TREB state exists; preserving it and skipping initial pull"
fi

status "starting" "Codespace claimed TREB end-to-end execution"
publish_status
echo "[$(ts)] TREB one-touch Codespaces build starting on $BRANCH"

python -m pip install --upgrade requests beautifulsoup4 pandas numpy pyarrow duckdb

# GitHub CLI must be authenticated so this owner can cancel stale hosted copies before work starts.
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  status "blocked" "GitHub CLI authentication unavailable; refusing to risk duplicate hosted execution"
  publish_status
  echo "GitHub CLI authentication unavailable. Build stopped before data collection to avoid duplicate execution."
  exit 74
fi

# Never race a hosted Stage 1/corrected-OFF job that genuinely started running.
ACTIVE_HOSTED="$(
  for wf in treb-stage1-historical.yml treb-corrected-off.yml; do
    gh run list --workflow "$wf" --branch "$BRANCH" --limit 50 --json databaseId,status \
      --jq '.[] | select(.status=="in_progress") | .databaseId' 2>/dev/null || true
  done | sed '/^$/d' | wc -l | tr -d ' '
)"
if [[ "${ACTIVE_HOSTED:-0}" -gt 0 ]]; then
  status "hosted_chain_active" "A hosted TREB job is already running; Codespace yielded to avoid duplication"
  publish_status
  echo "Hosted TREB execution is already active. Codespace will not start a competing chain."
  exit 75
fi

# Cancel stale queued hosted jobs and verify that cancellation has taken effect before local ownership.
queued_ids=()
for wf in treb-stage1-historical.yml treb-corrected-off.yml treb-runner-probe.yml; do
  while IFS= read -r run_id; do
    [[ -n "$run_id" ]] && queued_ids+=("$run_id")
  done < <(
    gh run list --workflow "$wf" --branch "$BRANCH" --limit 50 --json databaseId,status \
      --jq '.[] | select(.status=="queued") | .databaseId' 2>/dev/null || true
  )
done
if (( ${#queued_ids[@]} > 0 )); then
  for run_id in "${queued_ids[@]}"; do
    echo "Cancelling stale queued GitHub Actions run $run_id"
    gh run cancel "$run_id" >/dev/null 2>&1 || true
  done
  for _ in {1..30}; do
    remaining=0
    for run_id in "${queued_ids[@]}"; do
      s="$(gh run view "$run_id" --json status --jq '.status' 2>/dev/null || echo unknown)"
      if [[ "$s" == "queued" || "$s" == "in_progress" || "$s" == "unknown" ]]; then
        remaining=$((remaining + 1))
      fi
    done
    (( remaining == 0 )) && break
    sleep 4
  done
  if (( remaining != 0 )); then
    status "blocked" "$remaining stale hosted run(s) could not be cleared safely"
    publish_status
    echo "$remaining hosted run(s) remain active/unknown. Local build stopped to prevent duplicate work."
    exit 76
  fi
fi

# Reuse a clean exact-ready Stage 1 checkpoint if one already exists.
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
  publish_status
  echo "[$(ts)] Stage 1 exact-ready checkpoint absent; running full audited Stage 1 fallback"
  TREB_LOCAL_STAGE2=1 bash "$BASE/run_stage1_manual_fallback.sh"
fi

READY="$(python - "$ROSTER/tenure_review_queue_summary.json" <<'PY'
import json,sys
q=json.load(open(sys.argv[1]))
print('true' if q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0 else 'false')
PY
)"
if [[ "$READY" != "true" ]]; then
  status "stage1_review_required" "Stage 1 stopped correctly because the exact-ready review gate is not clean"
  publish_status
  echo "Stage 1 is not exact-ready. Corrected OFF has NOT started. Review evidence must be resolved first."
  exit 3
fi

status "stage2_selftest" "Validating corrected-OFF collector and finalizer"
publish_status
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
    echo "Batch attempt failed (streak=$consecutive_failures). Successful window cache remains durable."
    checkpoint "Checkpoint TREB corrected OFF after partial batch"
    if [[ $consecutive_failures -ge 4 ]]; then
      status "stage2_blocked" "Repeated corrected-OFF batch failures; successful cache preserved"
      checkpoint "Record TREB corrected OFF blocked status"
      echo "Stopping after repeated failures so methodology is not weakened. Resume is safe from existing cache."
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

# Backward-compatible summary key required by the finalizer.
python - "$CORRECTED/corrected_off_collection_summary.json" <<'PY'
import json,sys
p=sys.argv[1]
s=json.load(open(p))
if 'impact_windows_requested' not in s and 'impact_windows_total' in s:
    s['impact_windows_requested']=s['impact_windows_total']
open(p,'w',encoding='utf-8').write(json.dumps(s,indent=2))
PY

status "finalizing" "Building DuckDB/Parquet/CSV/README/audit/ZIP package"
checkpoint "Checkpoint TREB before final export"
echo "[$(ts)] All corrected-OFF tenure windows complete; building final verified package"
python "$BASE/finalize_corrected_off_package.py"

status "complete" "TREB corrected-OFF historical database built and verified"
checkpoint "Complete TREB corrected OFF historical database"

echo "[$(ts)] TREB_END_TO_END_COMPLETE=1"
echo "Final ZIP: $CORRECTED/TREB_corrected_off_final.zip"
