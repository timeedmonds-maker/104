#!/usr/bin/env bash
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-laptop-run"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
STATUS="$DB/laptop_supervisor_status.json"
FAILURE="$DB/laptop_failure_diagnostic.json"
TAIL="$DB/laptop_failure_tail.txt"
LOG="/tmp/treb_laptop_$(date -u +%Y%m%dT%H%M%SZ).log"
START="$(date +%s)"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: expected $EXPECTED_BRANCH, current ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-laptop-runner"
git config user.email "treb-laptop-runner@users.noreply.github.com"

push_selected () {
  local msg="$1"; shift
  git reset >/dev/null 2>&1 || true
  git add "$@" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "$msg [skip ci]" || true
    git push origin "HEAD:$BRANCH" || true
  fi
}

python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/laptop_supervisor_status.json')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({
  'generated_utc': datetime.now(timezone.utc).isoformat(),
  'state': 'started',
  'branch': 'treb-laptop-run',
  'runner': 'interactive Codespace/laptop',
  'purpose': 'self-diagnosing TREB completion run'
}, indent=2))
PY
push_selected "Record laptop supervisor start" "$STATUS"

echo "============================================================"
echo "TREB SELF-DIAGNOSING LAPTOP RUN"
echo "branch=$BRANCH"
echo "Any failure will automatically be pushed to GitHub."
echo "============================================================"

set +e
bash "$BASE/run_treb_laptop.sh" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

if [[ "$RC" -ne 0 ]]; then
  tail -n 250 "$LOG" > "$TAIL" 2>/dev/null || true
  RC="$RC" START="$START" LOG="$LOG" python - <<'PY'
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/laptop_failure_diagnostic.json')
tail=Path('team_trb_all_players/impact_database/laptop_failure_tail.txt')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({
  'generated_utc': datetime.now(timezone.utc).isoformat(),
  'state': 'failed',
  'exit_code': int(os.environ['RC']),
  'elapsed_seconds': int(time.time())-int(os.environ['START']),
  'branch': 'treb-laptop-run',
  'local_log': os.environ['LOG'],
  'diagnostic_tail_path': str(tail),
  'note': 'Failure diagnostic was automatically captured by run_treb_laptop_supervised.sh'
}, indent=2))
PY
  python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/laptop_supervisor_status.json')
p.write_text(json.dumps({'generated_utc':datetime.now(timezone.utc).isoformat(),'state':'failed','branch':'treb-laptop-run'},indent=2))
PY
  push_selected "Capture laptop run failure diagnostic" "$FAILURE" "$TAIL" "$STATUS"
  echo
  echo "TREB run failed with exit code $RC."
  echo "The diagnostic has been pushed automatically; no log copying is required."
  exit "$RC"
fi

python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/laptop_supervisor_status.json')
p.write_text(json.dumps({'generated_utc':datetime.now(timezone.utc).isoformat(),'state':'complete','branch':'treb-laptop-run'},indent=2))
PY
push_selected "Record laptop supervisor completion" "$STATUS"
echo "TREB supervised laptop run completed successfully."
