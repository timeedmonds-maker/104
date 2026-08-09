#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
EXPECTED_BRANCH="treb-stage2-local-pbp-canary"
BRANCH="$(git branch --show-current)"
BASE="team_trb_all_players"
RESULT="$BASE/impact_database/local_pbp_canary_result.json"
STATUS="$BASE/impact_database/local_pbp_canary_status.json"
DIAG="$BASE/impact_database/local_pbp_canary_diagnostic.log"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: expected $EXPECTED_BRANCH, current ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-codespace-runner"
git config user.email "treb-codespace-runner@users.noreply.github.com"

write_status () {
  PHASE="$1" NOTE="${2:-}" python - <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/local_pbp_canary_status.json')
p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(json.dumps({
 'generated_utc': datetime.now(timezone.utc).isoformat(),
 'phase': os.environ['PHASE'],
 'note': os.environ.get('NOTE') or None,
 'branch':'treb-stage2-local-pbp-canary',
 'mode':'isolated completed-core local PBP minutes canary; zero PBP Stats API calls; production Stage2 cache read-only'
},indent=2),encoding='utf-8')
PY
}

commit_result () {
  git add "$RESULT" "$STATUS" "$DIAG" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "Record completed-core local PBP canary result [skip ci]"
    git push origin "HEAD:$EXPECTED_BRANCH"
  fi
}

python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/corrected_off/corrected_off_collection_summary.json')
d=json.loads(p.read_text())
assert int(d.get('complete_windows') or 0) == 10112, d
assert int(d.get('remaining_windows') or 0) == 5315, d
print('BEST DURABLE STAGE2 BASE CONFIRMED: 10112 complete / 5315 remaining')
PY

write_status "setup" "Installing local bulk-PBP tooling; production Stage2 remains untouched"
python -m pip install --disable-pip-version-check -q "nba-on-court==0.2.1" pandas numpy

write_status "running" "Validating local lineup-derived minutes against accepted 2021-22 completed core"
rm -f "$DIAG" "$RESULT"
set +e
python "$BASE/local_pbp_core_canary.py" 2>&1 | tee "$DIAG"
RC=${PIPESTATUS[0]}
set -e

if [[ -s "$RESULT" ]]; then
  if (( RC == 0 )); then
    write_status "passed" "Local bulk-PBP minutes reproduced selected completed-core players with all team games resolved"
  else
    write_status "failed" "Completed-core local PBP canary ran but did not meet exact minutes gate; no production data changed"
  fi
  commit_result
else
  write_status "error" "Completed-core local PBP canary exited before result; diagnostic committed; no production data changed"
  git add "$STATUS" "$DIAG" 2>/dev/null || true
  git commit -m "Record completed-core local PBP canary diagnostic [skip ci]" || true
  git push origin "HEAD:$EXPECTED_BRANCH" || true
fi

exit "$RC"
