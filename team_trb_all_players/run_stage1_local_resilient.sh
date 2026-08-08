#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
ROSTER="$DB/roster_tenure"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

python -m pip install --upgrade requests beautifulsoup4

python "$BASE/normalize_roster_transactions.py" --self-test
python "$BASE/canonicalize_official_transaction_dates.py" --self-test
python "$BASE/build_roster_tenure_windows.py" --self-test
python "$BASE/fetch_regular_season_games.py" --self-test
python "$BASE/finalize_roster_tenure_windows.py" --self-test
python "$BASE/resolve_same_day_boundaries.py" --self-test
python "$BASE/resolve_same_day_roster_evidence.py" --self-test
python "$BASE/audit_roster_tenure_consistency.py" --self-test
python "$BASE/build_roster_tenure_review_queue.py" --self-test

# Historical source archive is resumable and cached season-by-season.
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

python "$BASE/normalize_roster_transactions.py"
# The NBA movement feed currently emits ISO datetimes. Canonicalize them before
# season assignment/tenure construction so Jan-Jun transactions are not mislabeled.
python "$BASE/canonicalize_official_transaction_dates.py"

# The first-pass builder intentionally emits a single interval per player/team/season.
# Repeat same-team stints can therefore appear as start>end until the dedicated splitter
# immediately below reconstructs the chronology. Accept only that known intermediate state.
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

# Zero-minute official tenures must be added BEFORE validating players who had no core minutes.
# Steven Adams played zero minutes in 2023-24, so his MEM/HOU trade boundary cannot exist in the
# core-derived first-pass windows; it is intentionally reconstructed here from official movement.
python "$BASE/augment_zero_minute_official_tenures.py"
python "$BASE/split_multi_stint_tenures.py"

# Remove the first-pass invalid-order marker only where the splitter has produced a
# chronologically valid interval. Any genuinely invalid interval remains fail-closed.
python - <<'PY'
import gzip,json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/player_team_season_windows.jsonl.gz')
rows=[]
stale=0
bad=[]
with gzip.open(p,'rt',encoding='utf-8') as f:
    for line in f:
        if not line.strip():
            continue
        r=json.loads(line)
        start=str(r.get('tenure_start') or '')
        end=str(r.get('tenure_end') or '')
        flags=list(r.get('audit_flags') or [])
        if start and end and start <= end and 'invalid_boundary_order' in flags:
            r['audit_flags']=[x for x in flags if x!='invalid_boundary_order']
            stale += 1
        if start and end and start > end:
            bad.append({k:r.get(k) for k in ('season','player_id','player_name','team_id','tenure_start','tenure_end','audit_flags')})
        rows.append(r)
if bad:
    print(json.dumps({'remaining_invalid_intervals':bad[:50],'count':len(bad)},indent=2))
    raise SystemExit('Repeat-stint splitter left genuinely invalid tenure intervals')
with gzip.open(p,'wt',encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r,ensure_ascii=False)+'\n')
print('POST-SPLIT CHRONOLOGY QA PASSED; stale invalid-order flags cleared=', stale)
PY

# Real-data regression QA for the zero-minute Steven Adams 2023-24 trade.
# This is deliberately after official zero-minute augmentation, not before it.
python - <<'PY'
import gzip,json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/player_team_season_windows.jsonl.gz')
rows=[json.loads(line) for line in gzip.open(p,'rt',encoding='utf-8') if line.strip()]
adams=[r for r in rows if r.get('season')=='2023-24' and str(r.get('player_id'))=='203500']
by_team={int(r['team_id']):r for r in adams}
assert 1610612763 in by_team, {'missing':'MEM','adams_rows':adams}
assert 1610612745 in by_team, {'missing':'HOU','adams_rows':adams}
assert by_team[1610612763]['tenure_end']=='2024-02-01', by_team[1610612763]
assert by_team[1610612745]['tenure_start']=='2024-02-01', by_team[1610612745]
print('ZERO-MINUTE TRADE QA PASSED: Steven Adams MEM->HOU 2024-02-01')
PY

python "$BASE/fetch_regular_season_games.py"
python "$BASE/finalize_roster_tenure_windows.py"
python "$BASE/resolve_same_day_boundaries.py"
python "$BASE/resolve_same_day_roster_evidence.py"

python "$BASE/audit_roster_tenure_consistency.py"
python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
c=json.loads((root/'tenure_consistency_summary.json').read_text())
assert c['window_count'] > 10000, c
assert c['invalid_interval_count'] == 0, c
assert c['duplicate_window_count'] == 0, c
assert c['resolved_game_count_inconsistency_count'] == 0, c
assert c['strict_cross_team_overlap_count'] == 0, c
assert c['strict_same_team_overlap_count'] == 0, c
print('FINAL ROSTER-TENURE CONSISTENCY QA PASSED', c)
PY

python "$BASE/build_roster_tenure_review_queue.py"
READY="$(python - <<'PY'
import json
from pathlib import Path
q=json.loads(Path('team_trb_all_players/impact_database/roster_tenure/tenure_review_queue_summary.json').read_text())
assert q['input_windows'] > 10000, q
assert isinstance(q['stage1_exact_ready'], bool), q
print('true' if q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0 else 'false')
PY
)"

echo "STAGE1_EXACT_READY=$READY"

git config user.name "treb-local-unattended"
git config user.email "treb-local-unattended@users.noreply.github.com"
git add "$DB/historical_transactions/basketball_reference_uniform" "$ROSTER" 2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "Advance resilient Stage 1 roster-tenure outputs [skip ci]"
  git push origin "HEAD:$BRANCH" || echo "WARNING: Stage 1 checkpoint committed locally; push can be retried later"
fi

if [[ "$READY" != "true" ]]; then
  echo "STAGE1_REVIEW_GATE_NOT_CLEAN=1"
  exit 3
fi

echo "STAGE1_LOCAL_RESILIENT_COMPLETE=1"
