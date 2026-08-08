#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BASE="team_trb_all_players"
TMP="/tmp/run_treb_laptop_v13_inner.sh"

# Reuse the durable v12 fast orchestration, but replace the targeted source
# recovery with the full-description-cell parser and use the runtime-safe v12
# boundary repair. This does not rerun the 780-team-season core or refetch the
# historical archive/schedule cache.
cp "$BASE/run_treb_laptop_v12_fast.sh" "$TMP"
sed -i 's#recover_overlap_events_from_bref_v12.py#recover_overlap_events_from_bref_v13.py#g' "$TMP"
sed -i 's#repair_overlap_boundaries_v12.py#repair_overlap_boundaries_v12_runtime.py#g' "$TMP"
sed -i 's/TREB V12 FAST RECOVERY/TREB V13 FAST RECOVERY/g' "$TMP"
sed -i 's/V12 STRICT OVERLAPS=/V13 STRICT OVERLAPS=/g' "$TMP"
sed -i 's/V12 STAGE 1 EXACT-READY QA PASSED/V13 STAGE 1 EXACT-READY QA PASSED/g' "$TMP"
sed -i 's/V12_STAGE1_EXACT_READY=1/V13_STAGE1_EXACT_READY=1/g' "$TMP"
chmod +x "$TMP"
exec bash "$TMP"
