#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
LOG_FILE="$ROOT/.git/impact_database_build.log"
LOCK_FILE="$ROOT/.git/impact_database_build.lock"

# Only one Codespace/local impact build may own this checkout at a time.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another impact database build already owns this Codespace checkout."
  exit 73
fi

# Codespace is the authoritative execution path. Cancel any overlapping
# impact/rebound/TRB/database GitHub Actions runs that may otherwise process
# stale checkpoints and overwrite Issue #9 status.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  for state in in_progress queued; do
    while IFS= read -r run_id; do
      [[ -z "$run_id" ]] && continue
      echo "Cancelling overlapping GitHub Actions run $run_id ($state)."
      gh run cancel "$run_id" --repo timeedmonds-maker/104 || true
    done < <(
      gh run list \
        --repo timeedmonds-maker/104 \
        --status "$state" \
        --limit 100 \
        --json databaseId,name,displayTitle \
        --jq '.[] | select(((.name // "") + " " + (.displayTitle // "")) | test("impact|rebound|team[ _-]?trb|database"; "i")) | .databaseId' \
        2>/dev/null || true
    )
  done
fi

# Prevent superseded local executors from writing while the corrected
# historical database build is running.
pkill -f 'team_trb_all_players/codespace_runner.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/direct_rebound_fast_build.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_build.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_build_fixed.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_supervisor.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_supervisor_fast.py' 2>/dev/null || true

git config user.name "github-codespaces[bot]"
git config user.email "codespaces@users.noreply.github.com"

# Preserve any completed local checkpoints left behind by an interrupted run
# before pulling the latest recovery code.
git add -A -- team_trb_all_players/impact_database
if ! git diff --cached --quiet; then
  git commit -m "Impact database recovery checkpoint" || true
fi

git pull --rebase origin main
git push origin HEAD:main || true
python -m pip install --disable-pip-version-check requests

python -m py_compile \
  team_trb_all_players/impact_database_build.py \
  team_trb_all_players/impact_database_build_fixed.py \
  team_trb_all_players/impact_database_runtime_tuning.py \
  team_trb_all_players/impact_database_single_owner.py \
  team_trb_all_players/impact_database_supervisor.py \
  team_trb_all_players/impact_database_supervisor_fast.py \
  team_trb_all_players/impact_database_preflight_fixed.py

python team_trb_all_players/impact_database_preflight_fixed.py

stop_requested=0
trap 'stop_requested=1' INT TERM

while (( stop_requested == 0 )); do
  set +e
  IMPACT_DB_STAGE=core python team_trb_all_players/impact_database_supervisor_fast.py 2>&1 | tee -a "$LOG_FILE"
  rc=${PIPESTATUS[0]}
  set -e

  if (( stop_requested != 0 )); then
    echo "Build stopped by user. Completed checkpoints remain saved."
    exit 130
  fi
  if (( rc == 0 )); then
    break
  fi

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Builder exited with code $rc; restarting from checkpoints in 20 seconds." | tee -a "$LOG_FILE"
  sleep 20
done

echo "Core historical player-impact database build finished and outputs were pushed. Teammate pairing was not run."
