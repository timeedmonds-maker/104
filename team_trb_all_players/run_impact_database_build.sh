#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
LOG_FILE="$ROOT/.git/impact_database_build.log"

# Prevent superseded executors from writing while the corrected historical
# database build is running.
pkill -f 'team_trb_all_players/codespace_runner.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/direct_rebound_fast_build.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_build.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_build_fixed.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_supervisor.py' 2>/dev/null || true

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
  team_trb_all_players/impact_database_supervisor.py \
  team_trb_all_players/impact_database_preflight_fixed.py

python team_trb_all_players/impact_database_preflight_fixed.py

stop_requested=0
trap 'stop_requested=1' INT TERM

while (( stop_requested == 0 )); do
  set +e
  IMPACT_DB_STAGE=core python team_trb_all_players/impact_database_supervisor.py 2>&1 | tee -a "$LOG_FILE"
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
