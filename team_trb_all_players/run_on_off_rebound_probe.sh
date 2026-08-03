#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
git pull --rebase origin main
python -m pip install --disable-pip-version-check requests
python team_trb_all_players/on_off_rebound_probe.py

git add team_trb_all_players/on_off_rebound_probe_output
if git diff --cached --quiet; then
  echo "Probe completed but produced no new repository changes."
else
  git config user.name "timeedmonds-maker"
  git config user.email "timeedmonds-maker@users.noreply.github.com"
  git commit -m "Record on-off rebound endpoint probe"
  git push origin HEAD:main
fi

echo "On-off rebound probe complete and evidence pushed."
