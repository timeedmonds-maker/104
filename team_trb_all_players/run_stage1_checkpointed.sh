#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
ROSTER="$DB/roster_tenure"
STATUS="$DB/stage1_checkpoint_status.json"
START_EPOCH="$(date +%s)"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

python - <<'PY' || python -m pip install requests beautifulsoup4
import requests, bs4
print('TREB Python dependencies already available')
PY

checkpoint () {
  local phase="$1"
  local phase_start="$2"
  local now elapsed total
  now="$(date +%s)"
  elapsed=$((now-phase_start))
  total=$((now-START_EPOCH))
  PHASE="$phase" ELAPSED="$elapsed" TOTAL="$total" python - <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/stage1_checkpoint_status.json')
try:
    d=json.loads(p.read_text()) if p.exists() else {}
except Exception:
    d={}
h=list(d.get('completed_phases') or [])
phase=os.environ['PHASE']
if phase not in h:
    h.append(phase)
d.update({
    'generated_utc': datetime.now(timezone.utc).isoformat(),
    'last_completed_phase': phase,
    'last_phase_elapsed_seconds': int(os.environ['ELAPSED']),
    'total_elapsed_seconds': int(os.environ['TOTAL']),
    'completed_phases': h,
    'methodology': 'transaction date belongs to departing team; incoming team effective next calendar day',
})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(d, indent=2))
print(json.dumps(d, indent=2))
PY
  git add "$DB/historical_transactions/basketball_reference_uniform" "$ROSTER" "$STATUS" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "Stage 1 checkpoint: ${phase} [skip ci]"
    git push origin "HEAD:$BRANCH"
  fi
}

# Cheap deterministic self-tests before network/data work.
python "$BASE/normalize_roster_transactions.py" --self-test
python "$BASE/canonicalize_official_transaction_dates.py" --self-test
python "$BASE/build_roster_tenure_windows.py" --self-test
python "$BASE/fetch_regular_season_games.py" --self-test
python "$BASE/finalize_roster_tenure_windows.py" --self-test
python "$BASE/resolve_same_day_boundaries.py" --self-test
python "$BASE/audit_roster_tenure_consistency.py" --self-test
python "$BASE/build_roster_tenure_review_queue.py" --self-test

# Phase 1: historical transaction archive. The collector itself reuses validated season caches.
P="$(date +%s)"
python "$BASE/fetch_bref_historical_transactions.py"
python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/historical_transactions/basketball_reference_uniform/manifest.json')
d=json.loads(p.read_text())
assert d['all_validated'] is True
assert d['completed_seasons'] == [f"{y}-{str(y+1)[-2:]}" for y in range(2000, 2016)]
assert d['total_rows'] > 3000
print('HISTORICAL ARCHIVE QA PASSED', d['total_rows'], 'transactions')
PY
checkpoint "historical_archive" "$P"

# Phase 2: normalized transactions and exact raw tenure chronology.
P="$(date +%s)"
python "$BASE/normalize_roster_transactions.py"
python "$BASE/canonicalize_official_transaction_dates.py"
set +e
python "$BASE/build_roster_tenure_windows.py"
BUILDER_RC=$?
set -e
python - "$BUILDER_RC" <<'PY'
import json,sys
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
s=json.loads((root/'tenure_window_summary.json').read_text())
rc=int(sys.argv[1])
assert s['window_count'] > 10000, s
assert len(s['seasons']) == 26, s['seasons']
invalid=int(s.get('invalid_boundary_order') or 0)
if rc != 0 and invalid == 0:
    raise SystemExit(f'Unexpected builder failure rc={rc} with no invalid-boundary intermediate rows')
if rc == 0 and invalid != 0:
    raise SystemExit(f'Builder reported success but summary still contains {invalid} invalid-boundary rows')
print('FIRST-PASS TENURE QA PASSED; repeat-stint intermediates=', invalid)
PY
python "$BASE/augment_zero_minute_official_tenures.py"
python "$BASE/split_multi_stint_tenures.py"
python - <<'PY'
import gzip,json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/player_team_season_windows.jsonl.gz')
rows=[]; stale=0; bad=[]
with gzip.open(p,'rt',encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        r=json.loads(line); start=str(r.get('tenure_start') or ''); end=str(r.get('tenure_end') or '')
        flags=list(r.get('audit_flags') or [])
        if start and end and start <= end and 'invalid_boundary_order' in flags:
            r['audit_flags']=[x for x in flags if x!='invalid_boundary_order']; stale += 1
        if start and end and start > end: bad.append(r)
        rows.append(r)
if bad: raise SystemExit(f'Repeat-stint splitter left {len(bad)} invalid tenure intervals')
with gzip.open(p,'wt',encoding='utf-8') as f:
    for r in rows: f.write(json.dumps(r,ensure_ascii=False)+'\n')
print('POST-SPLIT CHRONOLOGY QA PASSED; stale flags cleared=', stale)
PY
python - <<'PY'
import gzip,json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/player_team_season_windows.jsonl.gz')
rows=[json.loads(x) for x in gzip.open(p,'rt',encoding='utf-8') if x.strip()]
adams=[r for r in rows if r.get('season')=='2023-24' and str(r.get('player_id'))=='203500']
by={int(r['team_id']):r for r in adams}
assert by[1610612763]['tenure_end']=='2024-02-01'
assert by[1610612745]['tenure_start']=='2024-02-01'
print('ZERO-MINUTE TRADE RAW-DATE QA PASSED')
PY
checkpoint "tenure_chronology" "$P"

# Phase 3: regular-season schedules. fetch_regular_season_games.py now reuses valid per-season raw caches.
P="$(date +%s)"
python "$BASE/fetch_regular_season_games.py"
python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/regular_season_games_summary.json')
d=json.loads(p.read_text())
assert len(d['seasons']) == 26
assert d['game_count'] > 30000
print('SCHEDULE QA PASSED', d['game_count'], 'games; cache_hits=', d.get('cache_hits'), 'network_fetches=', d.get('network_fetches'))
PY
checkpoint "regular_season_schedules" "$P"

# Phase 4: deterministic effective windows, no same-day evidence network calls.
P="$(date +%s)"
python "$BASE/finalize_roster_tenure_windows.py"
python "$BASE/resolve_same_day_boundaries.py"
python - <<'PY'
import gzip,json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/player_team_season_windows_evidence_audited.jsonl.gz')
rows=[json.loads(x) for x in gzip.open(p,'rt',encoding='utf-8') if x.strip()]
adams=[r for r in rows if r.get('season')=='2023-24' and str(r.get('player_id'))=='203500']
by={int(r['team_id']):r for r in adams}; mem=by[1610612763]; hou=by[1610612745]
assert mem['query_end_date']=='2024-02-01' and mem['end_boundary_included'] is True
assert hou['query_start_date']=='2024-02-02' and hou['start_boundary_included'] is False
assert mem['schedule_boundary_status']=='resolved' and hou['schedule_boundary_status']=='resolved'
print('TRANSACTION-DAY POLICY QA PASSED')
PY
checkpoint "deterministic_effective_windows" "$P"

# Phase 5: strict consistency and exact-ready gate.
P="$(date +%s)"
python "$BASE/audit_roster_tenure_consistency.py"
python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/tenure_consistency_summary.json')
c=json.loads(p.read_text())
assert c['window_count'] > 10000
assert c['invalid_interval_count'] == 0
assert c['duplicate_window_count'] == 0
assert c['resolved_game_count_inconsistency_count'] == 0
assert c['strict_cross_team_overlap_count'] == 0
assert c['strict_same_team_overlap_count'] == 0
assert c.get('remaining_same_day_unresolved_count',0) == 0
print('FINAL ROSTER-TENURE CONSISTENCY QA PASSED')
PY
python "$BASE/build_roster_tenure_review_queue.py"
READY="$(python - <<'PY'
import json
from pathlib import Path
q=json.loads(Path('team_trb_all_players/impact_database/roster_tenure/tenure_review_queue_summary.json').read_text())
assert q['input_windows'] > 10000
assert isinstance(q['stage1_exact_ready'], bool)
print('true' if q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0 else 'false')
PY
)"
checkpoint "final_consistency_review_gate" "$P"
echo "STAGE1_EXACT_READY=$READY"
if [[ "$READY" != "true" ]]; then
  echo "STAGE1_REVIEW_GATE_NOT_CLEAN=1"
  exit 3
fi

echo "STAGE1_CHECKPOINTED_COMPLETE=1"
