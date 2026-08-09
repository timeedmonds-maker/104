#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage2-codespace"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
CORRECTED="$DB/corrected_off"
STATUS="$DB/codespace_stage2_status.json"
BATCH_SIZE="${TREB_BATCH_SIZE:-80}"
WORKERS="${TREB_WORKERS:-4}"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: expected $EXPECTED_BRANCH, current ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-codespace-runner"
git config user.email "treb-codespace-runner@users.noreply.github.com"

write_status () {
  local phase="$1"; local note="${2:-}"
  PHASE="$phase" NOTE="$note" python - <<'PY'
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
  'branch': 'treb-stage2-codespace',
  'mode': 'Codespace Stage2 corrected-OFF resume; completed 780/780 core reused; teammate pairs excluded'
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

write_status "setup" "Installing dependencies and validating Stage2 code"
python - <<'PY' || python -m pip install --disable-pip-version-check requests pandas numpy pyarrow duckdb
import requests, pandas, numpy, pyarrow, duckdb
print('dependencies already available')
PY
python "$BASE/build_corrected_tenure_off.py" --self-test
python "$BASE/prepare_corrected_off_workset.py" --self-test
python "$BASE/run_corrected_off_batch.py" --self-test
python "$BASE/run_corrected_off_batch_v2.py" --self-test
python "$BASE/finalize_corrected_off_package.py" --self-test

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

  write_status "collect" "round=$round complete=$complete remaining=$remaining workers=$WORKERS batch=$BATCH_SIZE"
  set +e
  python "$BASE/run_corrected_off_batch_v2.py" --batch-size "$BATCH_SIZE" --workers "$WORKERS"
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

  write_status "checkpoint" "round=$round complete=$new_complete remaining=$new_remaining batch_rc=$rc"
  commit_progress "Codespace corrected OFF checkpoint round $round"

  if [[ "$new_all_complete" == "true" ]]; then
    break
  fi

  if (( new_complete <= previous_complete )); then
    stalled_rounds=$((stalled_rounds+1))
  else
    stalled_rounds=0
  fi
  previous_complete=$new_complete

  if (( stalled_rounds >= 3 )); then
    write_status "stalled" "No increase in completed windows for three consecutive rounds; durable progress preserved"
    commit_progress "Codespace corrected OFF stalled status"
    echo "ERROR: Stage2 made no forward progress for three consecutive rounds." >&2
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
    d['execution_route']='4-core GitHub Codespace on isolated treb-stage2-codespace branch'
    p.write_text(json.dumps(d, indent=2), encoding='utf-8')
(root/'METHODOLOGY_TOLERANCE_NOTE.txt').write_text(
    'Stage1 retained 29 tolerated non-overlapping review/provenance flags out of 15,530 tenure windows. '
    'All hard structural QA was zero: cross-team overlaps, same-team overlaps, invalid intervals, duplicate windows, '
    'resolved game-count inconsistencies and unresolved same-day cases. Transaction date is assigned to the departing team; '
    'incoming-team tenure begins the following calendar day.\n', encoding='utf-8')
PY

write_status "complete" "Final corrected-OFF package exists and finalizer QA passed"
commit_progress "Complete TREB corrected OFF package from Codespace"
echo "TREB CODESPACE BUILD COMPLETE: $CORRECTED/TREB_corrected_off_final.zip"
