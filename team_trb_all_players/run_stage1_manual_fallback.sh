#!/usr/bin/env bash
set -euo pipefail

# Exact manual/Codespaces fallback for the TREB Stage 1 roster-tenure workflow.
# This intentionally mirrors .github/workflows/treb-stage1-historical.yml so that
# bypassing GitHub-hosted runner allocation does not weaken the methodology or QA.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

python -m pip install --upgrade requests beautifulsoup4

python team_trb_all_players/normalize_roster_transactions.py --self-test
python team_trb_all_players/build_roster_tenure_windows.py --self-test
python team_trb_all_players/fetch_regular_season_games.py --self-test
python team_trb_all_players/finalize_roster_tenure_windows.py --self-test
python team_trb_all_players/resolve_same_day_boundaries.py --self-test
python team_trb_all_players/resolve_same_day_roster_evidence.py --self-test
python team_trb_all_players/audit_roster_tenure_consistency.py --self-test
python team_trb_all_players/build_roster_tenure_review_queue.py --self-test

python team_trb_all_players/fetch_bref_historical_transactions.py
python - <<'PY'
import json
from pathlib import Path
p = Path('team_trb_all_players/impact_database/historical_transactions/basketball_reference_uniform/manifest.json')
d = json.loads(p.read_text())
assert d['all_validated'] is True
assert d['completed_seasons'] == [f"{y}-{str(y+1)[-2:]}" for y in range(2000, 2016)]
assert d['total_rows'] > 3000, d['total_rows']
print('HISTORICAL ARCHIVE QA PASSED', d['total_rows'], 'transactions')
PY

python team_trb_all_players/normalize_roster_transactions.py
python team_trb_all_players/build_roster_tenure_windows.py
python - <<'PY'
import gzip, json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
s=json.loads((root/'tenure_window_summary.json').read_text())
assert s['window_count'] > 10000, s
assert len(s['seasons']) == 26, s['seasons']
assert s['invalid_boundary_order'] == 0, s
rows=[json.loads(line) for line in gzip.open(root/'player_team_season_windows.jsonl.gz','rt',encoding='utf-8')]
adams=[r for r in rows if r['season']=='2023-24' and r['player_id']=='203500']
by_team={r['team_id']:r for r in adams}
assert 1610612763 in by_team and 1610612745 in by_team, adams
assert by_team[1610612763]['tenure_end']=='2024-02-01', by_team[1610612763]
assert by_team[1610612745]['tenure_start']=='2024-02-01', by_team[1610612745]
print('STAGE 1 STRUCTURAL QA PASSED', s)
PY

python team_trb_all_players/augment_zero_minute_official_tenures.py
python team_trb_all_players/split_multi_stint_tenures.py
python team_trb_all_players/fetch_regular_season_games.py
python team_trb_all_players/finalize_roster_tenure_windows.py
python team_trb_all_players/resolve_same_day_boundaries.py
python team_trb_all_players/resolve_same_day_roster_evidence.py
python team_trb_all_players/audit_roster_tenure_consistency.py
python team_trb_all_players/build_roster_tenure_review_queue.py

python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
games=json.loads((root/'regular_season_games_summary.json').read_text())
s=json.loads((root/'schedule_boundary_summary.json').read_text())
e=json.loads((root/'same_day_evidence_summary.json').read_text())
r=json.loads((root/'same_day_roster_evidence_summary.json').read_text())
c=json.loads((root/'tenure_consistency_summary.json').read_text())
q=json.loads((root/'tenure_review_queue_summary.json').read_text())
assert len(games['seasons']) == 26, games
assert games['game_count'] > 25000, games['game_count']
assert s['window_count'] > 10000, s
assert s['missing_team_schedules'] == 0, s
assert s['exact_team_game_count_windows'] > 9000, s
assert e['remaining_unresolved_windows'] <= e['input_unresolved_windows'], e
assert r['remaining_unresolved_windows'] <= r['input_unresolved_windows'], r
assert c['window_count'] > 10000, c
assert c['invalid_interval_count'] == 0, c
assert c['duplicate_window_count'] == 0, c
assert c['resolved_game_count_inconsistency_count'] == 0, c
assert c['strict_cross_team_overlap_count'] == 0, c
assert c['strict_same_team_overlap_count'] == 0, c
assert q['input_windows'] > 10000, q
assert isinstance(q['stage1_exact_ready'], bool), q
print('FINAL STAGE 1 QA PASSED')
print(json.dumps(q, indent=2))
PY

echo 'STAGE1_MANUAL_FALLBACK_COMPLETE=1'
