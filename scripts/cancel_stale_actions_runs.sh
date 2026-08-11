#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-timeedmonds-maker/104}"
KEEP_RUN_ID="${KEEP_RUN_ID:-31516744046}"
TMP_IDS="$(mktemp)"
trap 'rm -f "$TMP_IDS"' EXIT

printf 'Repository: %s\n' "$REPO"
printf 'Keeping production run: %s\n' "$KEEP_RUN_ID"
printf 'Collecting queued/pending/requested/waiting/in-progress workflow runs...\n'

: > "$TMP_IDS"
for status in queued pending requested waiting in_progress; do
  gh api --paginate \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "/repos/${REPO}/actions/runs?status=${status}&per_page=100" \
    --jq '.workflow_runs[].id' >> "$TMP_IDS" || true
done

sort -nu "$TMP_IDS" -o "$TMP_IDS"
TOTAL=$(wc -l < "$TMP_IDS" | tr -d ' ')
TARGETS=$(awk -v keep="$KEEP_RUN_ID" '$1 != keep {n++} END {print n+0}' "$TMP_IDS")

printf 'Found %s active/queued runs; %s will be cancelled; %s will be preserved.\n' "$TOTAL" "$TARGETS" "$KEEP_RUN_ID"

if [[ "$TARGETS" -eq 0 ]]; then
  echo 'Nothing to cancel.'
  exit 0
fi

cancel_one() {
  local run_id="$1"
  if [[ "$run_id" == "$KEEP_RUN_ID" ]]; then
    return 0
  fi

  if gh api --silent --method POST \
      -H 'Accept: application/vnd.github+json' \
      -H 'X-GitHub-Api-Version: 2022-11-28' \
      "/repos/${REPO}/actions/runs/${run_id}/cancel"; then
    printf 'cancelled %s\n' "$run_id"
    return 0
  fi

  # Fallback for stubborn in-progress runs.
  if gh api --silent --method POST \
      -H 'Accept: application/vnd.github+json' \
      -H 'X-GitHub-Api-Version: 2022-11-28' \
      "/repos/${REPO}/actions/runs/${run_id}/force-cancel"; then
    printf 'force-cancelled %s\n' "$run_id"
    return 0
  fi

  printf 'FAILED %s\n' "$run_id" >&2
  return 0
}
export -f cancel_one
export REPO KEEP_RUN_ID

awk -v keep="$KEEP_RUN_ID" '$1 != keep' "$TMP_IDS" | xargs -r -n1 -P4 bash -c 'cancel_one "$1"' _

echo 'Cancellation requests sent. Verifying remaining active/queued runs...'
sleep 3
for status in queued pending requested waiting in_progress; do
  count=$(gh api --paginate \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "/repos/${REPO}/actions/runs?status=${status}&per_page=100" \
    --jq '.workflow_runs[].id' 2>/dev/null | awk -v keep="$KEEP_RUN_ID" '$1 != keep {n++} END {print n+0}')
  printf '%-11s %s stale runs remaining\n' "$status" "$count"
done

echo 'Done.'
