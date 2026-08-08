#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
ROSTER="$DB/roster_tenure"
STATUS="$DB/stage1_checkpoint_status.json"
START="$(date +%s)"

if [[ "$BRANCH" != "treb-laptop-run" ]]; then
  echo "ERROR: expected treb-laptop-run, current ${BRANCH:-DETACHED}" >&2
  exit 2
fi

git config user.name "treb-laptop-runner"
git config user.email "treb-laptop-runner@users.noreply.github.com"

checkpoint () {
  local phase="$1"
  PHASE="$phase" START="$START" python - <<'PY'
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
p=Path('team_trb_all_players/impact_database/stage1_checkpoint_status.json')
try: d=json.loads(p.read_text()) if p.exists() else {}
except Exception: d={}
h=list(d.get('completed_phases') or [])
phase=os.environ['PHASE']
if phase not in h: h.append(phase)
d.update({
  'generated_utc':datetime.now(timezone.utc).isoformat(),
  'last_completed_phase':phase,
  'total_elapsed_seconds':int(time.time())-int(os.environ['START']),
  'completed_phases':h,
  'methodology':'transaction date belongs to departing team; incoming team effective next calendar day',
  'runner':'v12 targeted source recovery + event-state tenure reconciliation',
})
p.write_text(json.dumps(d,indent=2)); print(json.dumps(d,indent=2))
PY
  git add "$ROSTER" "$STATUS" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "Stage 1 v12 checkpoint: ${phase} [skip ci]"
    git push origin "HEAD:$BRANCH"
  fi
}

echo "============================================================"
echo "TREB V12 FAST RECOVERY"
echo "Uses durable v11 archive/schedules; no archive or core rerun."
echo "============================================================"

python "$BASE/recover_overlap_events_from_bref_v12.py" --self-test
python "$BASE/repair_overlap_boundaries_v12.py" --self-test
python "$BASE/reconcile_target_tenures_v12.py" --self-test
python - <<'PY'
import sys
sys.path.insert(0,'team_trb_all_players')
import split_multi_stint_tenures_v2 as m
m.self_test()
PY

# Recover exact transactions only for the 35 durable v11 overlap cases, then
# repair verified boundary anomalies. All source work is local/cached except the
# already-committed verified supplement; no live source fetch is required here.
python "$BASE/recover_overlap_events_from_bref_v12.py"
python "$BASE/repair_overlap_boundaries_v12.py"
checkpoint "v12_targeted_source_recovery"

# Rebuild only the roster-tenure layer from the repaired transaction stream.
set +e
python "$BASE/build_roster_tenure_windows.py"
BUILDER_RC=$?
set -e
python "$BASE/augment_zero_minute_official_tenures.py"
python "$BASE/split_multi_stint_tenures_v2.py"
python "$BASE/reconcile_target_tenures_v12.py"
python "$BASE/reconcile_roster_window_confidence.py"

python - "$BUILDER_RC" <<'PY'
import gzip,json,sys
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/player_team_season_windows.jsonl.gz')
rows=[]; bad=[]; stale=0
with gzip.open(p,'rt',encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        r=json.loads(line); a=str(r.get('tenure_start') or ''); b=str(r.get('tenure_end') or '')
        flags=list(r.get('audit_flags') or [])
        if a and b and a <= b and 'invalid_boundary_order' in flags:
            r['audit_flags']=[x for x in flags if x!='invalid_boundary_order']; stale += 1
        if a and b and a > b: bad.append(r)
        rows.append(r)
if bad: raise SystemExit(f'V12 chronology left {len(bad)} invalid intervals')
with gzip.open(p,'wt',encoding='utf-8') as f:
    for r in rows: f.write(json.dumps(r,ensure_ascii=False)+'\n')
print('V12 RAW CHRONOLOGY BUILT',len(rows),'windows; stale flags cleared=',stale,'builder_rc=',sys.argv[1])
PY
checkpoint "v12_tenure_chronology"

# The 31,254-game schedule cache is already durable; recompute only effective
# query windows and strict QA from the new raw chronology.
test -s "$ROSTER/regular_season_games.jsonl.gz"
python "$BASE/finalize_roster_tenure_windows.py"
python "$BASE/resolve_same_day_boundaries.py"
python "$BASE/audit_roster_tenure_consistency.py"
python "$BASE/diagnose_remaining_overlap_event_chains_v12.py"
checkpoint "v12_overlap_diagnostic"

python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/tenure_consistency_summary.json')
d=json.loads(p.read_text())
print('V12 STRICT OVERLAPS=',d.get('strict_cross_team_overlap_count'),'REVIEW=',(d.get('confidence_counts') or {}).get('review'))
assert d['window_count'] > 10000,d
assert d['invalid_interval_count'] == 0,d
assert d['duplicate_window_count'] == 0,d
assert d['resolved_game_count_inconsistency_count'] == 0,d
assert d['strict_same_team_overlap_count'] == 0,d
assert d.get('remaining_same_day_unresolved_count',0) == 0,d
assert d['strict_cross_team_overlap_count'] == 0,d
PY

python "$BASE/build_roster_tenure_review_queue.py"
python - <<'PY'
import json
from pathlib import Path
p=Path('team_trb_all_players/impact_database/roster_tenure/tenure_review_queue_summary.json')
d=json.loads(p.read_text())
assert d.get('stage1_exact_ready') is True,d
assert int(d.get('review_queue_windows') or 0) == 0,d
print('V12 STAGE 1 EXACT-READY QA PASSED')
PY
checkpoint "final_consistency_review_gate"
echo "V12_STAGE1_EXACT_READY=1"

# Continue automatically: successful supervisor completion means the final
# corrected-OFF package exists and finalizer QA has passed, not merely Stage 1.
bash "$BASE/run_treb_stage2_resume.sh"
