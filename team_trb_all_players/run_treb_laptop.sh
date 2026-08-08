#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-laptop-run"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
CORRECTED="$DB/corrected_off"
STATUS="$DB/laptop_run_status.json"
START_EPOCH="$(date +%s)"
BATCH_SIZE="${TREB_BATCH_SIZE:-100}"
WORKERS="${TREB_WORKERS:-4}"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: expected branch $EXPECTED_BRANCH, current branch is ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-laptop-runner"
git config user.email "treb-laptop-runner@users.noreply.github.com"

write_status () {
  local phase="$1"
  local note="${2:-}"
  PHASE="$phase" NOTE="$note" START_EPOCH="$START_EPOCH" python - <<'PY'
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/laptop_run_status.json')
try:
    d=json.loads(p.read_text()) if p.exists() else {}
except Exception:
    d={}
d.update({
    'generated_utc': datetime.now(timezone.utc).isoformat(),
    'phase': os.environ['PHASE'],
    'note': os.environ.get('NOTE') or None,
    'elapsed_seconds': int(time.time())-int(os.environ['START_EPOCH']),
    'branch': 'treb-laptop-run',
    'mode': 'persistent laptop runner',
})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(d, indent=2))
print(json.dumps(d, indent=2))
PY
}

commit_paths () {
  local message="$1"
  shift
  git add "$@" 2>/dev/null || true
  git add "$STATUS" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "$message [skip ci]"
    git push origin "HEAD:$BRANCH"
  fi
}

echo "============================================================"
echo "TREB persistent laptop build"
echo "branch=$BRANCH batch_size=$BATCH_SIZE workers=$WORKERS"
echo "Do not close this terminal while it is running."
echo "============================================================"

write_status "starting" "Installing only dependencies needed for Stage 1"
python - <<'PY' || python -m pip install --disable-pip-version-check requests beautifulsoup4
import requests, bs4
print('Stage 1 dependencies already available')
PY

# The shared Stage 1 runner intentionally guards the automation branch. For the
# isolated laptop branch, execute an exact temporary copy with only that branch
# guard changed. All data logic and QA remain identical.
sed 's/EXPECTED_BRANCH="treb-stage1-automation"/EXPECTED_BRANCH="treb-laptop-run"/' \
  "$BASE/run_stage1_checkpointed.sh" > /tmp/run_stage1_laptop.sh
chmod +x /tmp/run_stage1_laptop.sh

write_status "stage1" "Building exact roster-tenure windows with durable per-season pushes"
bash /tmp/run_stage1_laptop.sh

python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/tenure_review_queue_summary.json')
d=json.loads(p.read_text())
assert d.get('stage1_exact_ready') is True, d
assert int(d.get('review_queue_windows') or 0) == 0, d
print('LAPTOP STAGE 1 EXACT-READY QA PASSED')
PY
write_status "stage1_complete" "Stage 1 exact-ready and review queue is zero"
commit_paths "Laptop Stage 1 exact-ready" "$DB/roster_tenure" "$DB/historical_transactions/basketball_reference_uniform"

# Install heavier dependencies only after Stage 1 has passed.
write_status "stage2_setup" "Installing Stage 2/finalizer dependencies once"
python - <<'PY' || python -m pip install --disable-pip-version-check requests pandas numpy pyarrow duckdb
import requests, pandas, numpy, pyarrow, duckdb
print('Stage 2 dependencies already available')
PY

python "$BASE/build_corrected_tenure_off.py" --self-test
python "$BASE/prepare_corrected_off_workset.py" --self-test
python "$BASE/run_corrected_off_batch.py" --self-test
python "$BASE/finalize_corrected_off_package.py" --self-test

write_status "stage2_workset" "Preparing exact correction workset and reusing completed core where valid"
python "$BASE/prepare_corrected_off_workset.py"
commit_paths "Laptop corrected OFF workset" "$CORRECTED"

previous_complete=-1
stalled_rounds=0
round=0
while true; do
  round=$((round+1))
  read -r complete remaining all_complete < <(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
if p.exists():
    d=json.loads(p.read_text())
    print(int(d.get('complete_windows') or 0), int(d.get('remaining_windows') or 0), 'true' if d.get('all_complete') else 'false')
else:
    print(0, -1, 'false')
PY
  )

  if [[ "$all_complete" == "true" ]]; then
    break
  fi

  write_status "stage2_collect" "round=$round complete=$complete remaining=$remaining workers=$WORKERS batch=$BATCH_SIZE"
  set +e
  python "$BASE/run_corrected_off_batch.py" --batch-size "$BATCH_SIZE" --workers "$WORKERS"
  rc=$?
  set -e

  read -r new_complete new_remaining new_all_complete < <(python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
print(int(d.get('complete_windows') or 0), int(d.get('remaining_windows') or 0), 'true' if d.get('all_complete') else 'false')
PY
  )

  write_status "stage2_checkpoint" "round=$round complete=$new_complete remaining=$new_remaining batch_rc=$rc"
  commit_paths "Laptop corrected OFF checkpoint round $round" "$CORRECTED"

  if [[ "$new_all_complete" == "true" ]]; then
    break
  fi

  if (( new_complete <= previous_complete )); then
    stalled_rounds=$((stalled_rounds+1))
  else
    stalled_rounds=0
  fi
  previous_complete=$new_complete

  # Isolated request failures are allowed to retry while other windows continue
  # to become durable. Stop only after three consecutive rounds with zero forward
  # progress, which prevents an infinite loop on a persistent systemic failure.
  if (( stalled_rounds >= 3 )); then
    write_status "stage2_stalled" "No increase in completed windows for three rounds; durable progress preserved"
    commit_paths "Laptop corrected OFF stalled status" "$CORRECTED"
    echo "ERROR: Stage 2 made no forward progress for three consecutive rounds." >&2
    exit 4
  fi

done

# Finalizer compatibility key retained from the Actions workflow.
python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
if 'impact_windows_requested' not in d and 'impact_windows_total' in d:
    d['impact_windows_requested']=d['impact_windows_total']
p.write_text(json.dumps(d, indent=2))
assert d.get('all_complete') is True, d
assert int(d.get('remaining_windows') or 0) == 0, d
print('CORRECTED OFF COLLECTION COMPLETE')
PY

write_status "finalizing" "All corrected OFF windows collected; building final validated package"
python "$BASE/finalize_corrected_off_package.py"

test -s "$CORRECTED/TREB_corrected_off_final.zip"
write_status "complete" "Final corrected OFF package exists and finalizer QA passed"
commit_paths "Complete TREB corrected OFF package from laptop" "$CORRECTED"

echo "============================================================"
echo "TREB LAPTOP BUILD COMPLETE"
echo "Final package: $CORRECTED/TREB_corrected_off_final.zip"
echo "============================================================"
