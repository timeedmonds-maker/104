#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Prevent superseded rebound-only executors from writing while this broader
# historical database build is running.
pkill -f 'team_trb_all_players/codespace_runner.py' 2>/dev/null || true
pkill -f 'team_trb_all_players/direct_rebound_fast_build.py' 2>/dev/null || true

git pull --rebase origin main
python -m pip install --disable-pip-version-check requests

python team_trb_all_players/impact_database_preflight.py
python team_trb_all_players/impact_database_build.py

echo "Historical player-impact database build finished and outputs were pushed."
