#!/usr/bin/env bash
set -euo pipefail

# Exact manual/Codespaces fallback for the TREB Stage 1 roster-tenure workflow.
# This mirrors .github/workflows/treb-stage1-historical.yml closely so bypassing
# GitHub-hosted runner allocation does not weaken methodology, QA, auditability,
# or the Stage 1 -> corrected-OFF handoff.
#
# Safe defaults:
#   * all generated-data QA is fail-closed;
#   * corrected OFF is dispatched only when stage1_exact_ready=true AND queue=0;
#   * when TREB_LOCAL_STAGE2=1, hosted dispatch is deliberately suppressed because
#     the owning Codespace continues directly into the local resumable Stage 2;
#   * generated Stage 1 outputs are committed only when this is a real git checkout.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: fallback must run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

python -m pip install --upgrade requests beautifulsoup4

# ---------------------------------------------------------------------------
# Self-tests: exact Stage 1 components only; completed core/on-court is untouched.
# ---------------------------------------------------------------------------
python team_trb_all_players/normalize_roster_transactions.py --self-test
python team_trb_all_players/build_roster_tenure_windows.py --self-test
python team_trb_all_players/fetch_regular_season_games.py --self-test
python team_trb_all_players/finalize_roster_tenure_windows.py --self-test
python team_trb_all_players/resolve_same_day_boundaries.py --self-test
python team_trb_all_players/resolve_same_day_roster_evidence.py --self-test
python team_trb_all_players/audit_roster_tenure_consistency.py --self-test
python team_trb_all_players/build_roster_tenure_review_queue.py --self-test

# ---------------------------------------------------------------------------
# Historical transactions + structural tenure windows.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Zero-minute official tenures and repeat same-team stints.
# ---------------------------------------------------------------------------
python team_trb_all_players/augment_zero_minute_official_tenures.py
python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
s=json.loads((root/'zero_minute_official_summary.json').read_text())
assert s['added_windows'] >= 0, s
assert s['total_windows_after_augmentation'] > 10000, s
print('ZERO-MINUTE OFFICIAL TENURE QA PASSED', s)
PY

python team_trb_all_players/split_multi_stint_tenures.py
python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
s=json.loads((root/'multi_stint_summary.json').read_text())
assert s['output_windows'] >= s['input_windows'], s
assert s['extra_segments_created'] >= 0, s
print('MULTI-STINT TENURE QA PASSED', s)
PY

# ---------------------------------------------------------------------------
# Exact schedules, same-day positive evidence, and final consistency audit.
# ---------------------------------------------------------------------------
python team_trb_all_players/fetch_regular_season_games.py
python team_trb_all_players/finalize_roster_tenure_windows.py
python team_trb_all_players/resolve_same_day_boundaries.py
python team_trb_all_players/resolve_same_day_roster_evidence.py

python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
games=json.loads((root/'regular_season_games_summary.json').read_text())
s=json.loads((root/'schedule_boundary_summary.json').read_text())
e=json.loads((root/'same_day_evidence_summary.json').read_text())
r=json.loads((root/'same_day_roster_evidence_summary.json').read_text())
assert len(games['seasons']) == 26, games
assert games['game_count'] > 25000, games['game_count']
assert s['window_count'] > 10000, s
assert s['missing_team_schedules'] == 0, s
assert s['exact_team_game_count_windows'] > 9000, s
assert e['remaining_unresolved_windows'] <= e['input_unresolved_windows'], e
assert r['remaining_unresolved_windows'] <= r['input_unresolved_windows'], r
assert 'non-participation' in e['method'], e
assert 'absence' in r['method'], r
print('SCHEDULE-AWARE STAGE 1 QA PASSED', s)
print('SAME-DAY POSITIVE-PARTICIPATION AUDIT PASSED', e)
print('SAME-DAY POSITIVE-ROSTER AUDIT PASSED', r)
PY

python team_trb_all_players/audit_roster_tenure_consistency.py
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

# ---------------------------------------------------------------------------
# Consolidated review queue: exact-ready is the hard Stage 2 gate.
# ---------------------------------------------------------------------------
python team_trb_all_players/build_roster_tenure_review_queue.py
READY="$(python - <<'PY'
import json
from pathlib import Path
root=Path('team_trb_all_players/impact_database/roster_tenure')
q=json.loads((root/'tenure_review_queue_summary.json').read_text())
assert q['input_windows'] > 10000, q
assert q['review_queue_windows'] >= 0, q
assert isinstance(q['stage1_exact_ready'], bool), q
assert 'non-participation' in q['policy'], q
ready=q.get('stage1_exact_ready') is True and int(q.get('review_queue_windows') or 0)==0
print('true' if ready else 'false')
PY
)"

echo "STAGE1_EXACT_READY=$READY"

# Persist exact Stage 1 generated outputs so the fallback remains fully auditable.
git config user.name "treb-stage1-fallback"
git config user.email "treb-stage1-fallback@users.noreply.github.com"
git add team_trb_all_players/impact_database/historical_transactions/basketball_reference_uniform
git add team_trb_all_players/impact_database/roster_tenure
if git diff --cached --quiet; then
  echo "No generated Stage 1 changes to commit"
else
  git commit -m "Generate Stage 1 roster tenure audit outputs via fallback [skip ci]"
  git push origin "HEAD:$BRANCH"
fi

# If the caller owns local Stage 2, never create a redundant hosted chain.
if [[ "${TREB_LOCAL_STAGE2:-0}" == "1" ]]; then
  echo "TREB_LOCAL_STAGE2=1; hosted corrected-OFF dispatch intentionally skipped"
elif [[ "$READY" == "true" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "STAGE1_READY_DISPATCH_PENDING=1 (gh CLI unavailable)"
  else
    ACTIVE="$(gh run list --workflow treb-corrected-off.yml --branch "$BRANCH" --limit 20 --json status --jq '[.[] | select(.status=="queued" or .status=="in_progress" or .status=="waiting" or .status=="pending")] | length')"
    if [[ "${ACTIVE:-0}" -gt 0 ]]; then
      echo "Corrected OFF already has an active run; duplicate dispatch skipped"
    else
      echo "Stage 1 exact-ready; dispatching corrected OFF"
      gh workflow run treb-corrected-off.yml --ref "$BRANCH"
    fi
  fi
else
  echo "Stage 1 review queue is not empty; corrected OFF remains correctly gated"
fi

echo 'STAGE1_MANUAL_FALLBACK_COMPLETE=1'
