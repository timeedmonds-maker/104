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
TMP_CANARY="/tmp/local_pbp_treb_canary_runtime.py"

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
 'mode':'isolated public bulk PBP canary; zero PBP Stats production API calls; production Stage2 cache is read-only'
},indent=2),encoding='utf-8')
PY
}

commit_result () {
  git add "$RESULT" "$STATUS" "$DIAG" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "Record local bulk-PBP TREB canary result [skip ci]"
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

write_status "setup" "Installing local bulk-PBP tooling; no production Stage2 collection running"
python -m pip install --disable-pip-version-check -q "nba-on-court==0.2.1" pandas numpy

# The first canary hard-coded 2007-08, but this branch has no completed partial-tenure
# caches from that season. Select a season from the data we actually possess.
# Restrict to 2000-01..2021-22 for conservative compatibility with the public bulk archive.
read -r CANARY_SEASON CANARY_YEAR CANARY_COUNT < <(python - <<'PY'
import gzip, json, re
from collections import Counter
from pathlib import Path
cache=Path('team_trb_all_players/impact_database/corrected_off/cache')
counts=Counter()
for p in cache.glob('*.json.gz'):
    m=re.match(r'^(\d{4})-(\d{2})__', p.name)
    if not m:
        continue
    year=int(m.group(1))
    if year < 2000 or year > 2021:
        continue
    try:
        with gzip.open(p,'rt',encoding='utf-8') as f: d=json.load(f)
    except Exception:
        continue
    if d.get('complete') is not True:
        continue
    start=str(d.get('query_start_date') or '')
    end=str(d.get('query_end_date') or '')
    metrics=d.get('metrics')
    if not start or not end or start >= end or not isinstance(metrics,list) or not metrics:
        continue
    names={str(x.get('metric') or '') for x in metrics if isinstance(x,dict)}
    if not {'OffRebounds','DefRebounds'} <= names:
        continue
    if int(d.get('team_games_in_window') or 0) < 1 or float(d.get('minutes_on') or 0) <= 0:
        continue
    counts[f'{year}-{str(year+1)[-2:]}'] += 1
eligible=[(n,s) for s,n in counts.items() if n >= 3]
if not eligible:
    raise SystemExit('NO_ELIGIBLE_COMPLETED_PARTIAL_SEASON')
# Prefer the season with the most available completed canaries; tie-break to earlier year.
n,season=sorted(eligible,key=lambda x:(-x[0], int(x[1][:4])))[0]
print(season, int(season[:4]), n)
PY
)

echo "AUTO-SELECTED CANARY SEASON: $CANARY_SEASON ($CANARY_COUNT completed eligible partial windows)"

# Run an isolated temporary copy with the selected season. Production code/cache remains untouched.
python - "$CANARY_SEASON" "$CANARY_YEAR" <<'PY'
from pathlib import Path
import re, sys
season=sys.argv[1]; year=sys.argv[2]
src=Path('team_trb_all_players/local_pbp_treb_canary.py').read_text(encoding='utf-8')
src=re.sub(r'^SEASON\s*=\s*"[^"]+"', f'SEASON = "{season}"', src, count=1, flags=re.M)
src=re.sub(r'^START_YEAR\s*=\s*\d+', f'START_YEAR = {year}', src, count=1, flags=re.M)
Path('/tmp/local_pbp_treb_canary_runtime.py').write_text(src,encoding='utf-8')
PY

write_status "running" "Downloading public bulk season $CANARY_SEASON and validating already-completed partial-tenure windows"
rm -f "$DIAG" "$RESULT"
set +e
PYTHONPATH="$BASE:${PYTHONPATH:-}" python "$TMP_CANARY" 2>&1 | tee "$DIAG"
RC=${PIPESTATUS[0]}
set -e

if [[ -s "$RESULT" ]]; then
  if (( RC == 0 )); then
    write_status "passed" "Local bulk-PBP canary reproduced selected completed windows in $CANARY_SEASON; safe to design full local Stage2 replacement"
  else
    write_status "failed" "Local bulk-PBP canary ran on $CANARY_SEASON but did not reproduce every selected window; result and diagnostics committed; no production data changed"
  fi
  commit_result
else
  write_status "error" "Canary exited before producing a result file for $CANARY_SEASON; diagnostic log committed; no production data changed"
  git add "$STATUS" "$DIAG" 2>/dev/null || true
  git commit -m "Record local bulk-PBP canary diagnostic error [skip ci]" || true
  git push origin "HEAD:$EXPECTED_BRANCH" || true
fi

exit "$RC"
