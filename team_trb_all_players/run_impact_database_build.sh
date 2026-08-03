#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Prevent superseded executors from writing while the corrected historical
# database build is running.
pkill -f 'team_trb_all_players/codespace_runner.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/direct_rebound_fast_build.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/impact_database_build.py' 2>/dev/null || true

git pull --rebase origin main
python -m pip install --disable-pip-version-check requests

python -m py_compile \
  team_trb_all_players/impact_database_build.py \
  team_trb_all_players/impact_database_build_fixed.py \
  team_trb_all_players/impact_database_preflight_fixed.py

python team_trb_all_players/impact_database_preflight_fixed.py
python team_trb_all_players/impact_database_build_fixed.py

echo "Historical player-impact database build finished and outputs were pushed."
