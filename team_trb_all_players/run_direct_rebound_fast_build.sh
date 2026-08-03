#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
git pull --rebase origin main
python -m pip install --disable-pip-version-check requests
python team_trb_all_players/direct_rebound_fast_build.py

echo "Direct rebound production build finished; checkpoints and outputs were pushed during the run."
