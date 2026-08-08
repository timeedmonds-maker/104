#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
TMP="/tmp/run_treb_laptop_v12_ready_inner.sh"

cp "$BASE/run_treb_laptop_v12_fast.sh" "$TMP"
sed -i 's#repair_overlap_boundaries_v12.py#repair_overlap_boundaries_v12_runtime.py#g' "$TMP"
chmod +x "$TMP"
exec bash "$TMP"
