#!/usr/bin/env bash
set -euo pipefail

# Fast recovery launcher for TREB Codespaces after a large backlog of stale
# GitHub Actions runs has already been cancelled. It verifies hosted execution
# by re-listing workflows in bulk instead of serially polling every historical
# run ID, then hands back to the authoritative end-to-end runner.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: GitHub CLI is unavailable or unauthenticated" >&2
  exit 74
fi

workflows=(treb-stage1-historical.yml treb-corrected-off.yml treb-runner-probe.yml)

count_status() {
  local wanted="$1"
  local total=0 wf n
  for wf in "${workflows[@]}"; do
    n="$(gh run list --workflow "$wf" --branch "$BRANCH" --limit 100 --json status \
      --jq "[.[] | select(.status==\"$wanted\")] | length" 2>/dev/null || echo 0)"
    total=$((total + ${n:-0}))
  done
  echo "$total"
}

running="$(count_status in_progress)"
if (( running > 0 )); then
  echo "ERROR: $running hosted TREB run(s) are genuinely in progress; refusing duplicate execution" >&2
  exit 75
fi

for attempt in 1 2 3 4 5 6; do
  queued=0
  for wf in "${workflows[@]}"; do
    while IFS= read -r run_id; do
      [[ -z "$run_id" ]] && continue
      queued=$((queued + 1))
      echo "Cancelling queued hosted run $run_id"
      gh run cancel "$run_id" >/dev/null 2>&1 || true
    done < <(
      gh run list --workflow "$wf" --branch "$BRANCH" --limit 100 --json databaseId,status \
        --jq '.[] | select(.status=="queued") | .databaseId' 2>/dev/null || true
    )
  done

  running="$(count_status in_progress)"
  if (( running > 0 )); then
    echo "ERROR: a hosted TREB run started while cleaning queue; refusing duplicate execution" >&2
    exit 75
  fi

  remaining="$(count_status queued)"
  echo "Hosted queue verification attempt $attempt: queued=$remaining"
  if (( remaining == 0 )); then
    echo "Hosted TREB queue is clear. Starting authoritative Codespace pipeline now."
    exec bash team_trb_all_players/run_treb_end_to_end_codespace.sh
  fi
  sleep 5
done

echo "ERROR: hosted TREB queue still reports $remaining queued run(s) after bulk cancellation checks" >&2
exit 76
