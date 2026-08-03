#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "Updating repository..."
git pull --rebase origin main

python -m pip install --disable-pip-version-check requests
python team_trb_all_players/direct_player_probe.py

git add team_trb_all_players/direct_player_probe_output
if git diff --cached --quiet; then
  echo "Probe completed but produced no new repository changes."
else
  git config user.name "timeedmonds-maker"
  git config user.email "timeedmonds-maker@users.noreply.github.com"
  git commit -m "Record direct-player endpoint probe"
  git push origin HEAD:main
fi

echo "Direct-player probe complete and evidence pushed."
