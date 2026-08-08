#!/usr/bin/env bash
set -u

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
EXPECTED_BRANCH="treb-stage1-automation"
BASE="team_trb_all_players"
DB="$BASE/impact_database"
RUNNER="$BASE/run_treb_local_unattended.sh"
SUP_LOG="$DB/treb_self_healing_supervisor.log"
FAIL_REPORT="$DB/treb_failure_report.txt"
STATUS="$DB/treb_supervisor_status.json"
LOCK="$DB/.treb_self_healing_supervisor.lock"
POLL_SECONDS="${TREB_SUPERVISOR_POLL_SECONDS:-60}"
HEARTBEAT_SECONDS="${TREB_HEARTBEAT_SECONDS:-240}"

mkdir -p "$DB"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "TREB self-healing supervisor is already running in this Codespace."
  exit 0
fi

exec > >(tee -a "$SUP_LOG") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
write_status() {
  python - "$STATUS" "$1" "${2:-}" <<'PY'
import json,sys,datetime
p,phase,detail=sys.argv[1:4]
open(p,'w',encoding='utf-8').write(json.dumps({
    'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'phase': phase,
    'detail': detail,
}, indent=2))
PY
}

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "ERROR: supervisor must run from $EXPECTED_BRANCH (current: ${BRANCH:-DETACHED})" >&2
  exit 2
fi

git config user.name "treb-self-healing-supervisor"
git config user.email "treb-self-healing-supervisor@users.noreply.github.com"

heartbeat() {
  while true; do
    sleep "$HEARTBEAT_SECONDS"
    echo "[$(ts)] TREB_SUPERVISOR_HEARTBEAT=1"
  done
}
heartbeat &
HB_PID=$!
trap 'kill "$HB_PID" 2>/dev/null || true' EXIT

publish_failure() {
  local rc="$1"
  {
    echo "TREB SELF-HEALING FAILURE REPORT"
    echo "generated_utc=$(ts)"
    echo "runner_rc=$rc"
    echo "branch=$BRANCH"
    echo "local_head=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    echo
    echo "===== GIT STATUS ====="
    git status --short || true
    echo
    echo "===== LAST 300 LINES OF UNATTENDED LOG ====="
    tail -n 300 "$DB/treb_local_unattended.log" 2>/dev/null || echo "unattended log not found"
    echo
    echo "===== LAST 120 LINES OF SUPERVISOR LOG ====="
    tail -n 120 "$SUP_LOG" 2>/dev/null || true
  } > "$FAIL_REPORT"

  write_status "waiting_for_remote_fix" "runner_rc=$rc; failure report published; supervisor polling for branch update"
  git add "$FAIL_REPORT" "$STATUS" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "Publish TREB failure diagnostics [skip ci]" || true
    for attempt in 1 2 3; do
      if git push origin "HEAD:$BRANCH"; then
        break
      fi
      git fetch origin "$BRANCH" || true
      sleep $((10 * attempt))
    done
  fi
}

pull_remote_fix() {
  git fetch origin "$BRANCH" || return 1
  local local_head remote_head
  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "origin/$BRANCH")"
  [[ "$remote_head" != "$local_head" ]] || return 2

  echo "[$(ts)] Remote branch advanced: $local_head -> $remote_head"
  if git pull --ff-only origin "$BRANCH"; then
    return 0
  fi

  echo "[$(ts)] Fast-forward pull blocked by local generated state; attempting code-only refresh"
  # Preserve all generated data. Refresh only tracked source/runner files that changed remotely.
  while IFS= read -r path; do
    case "$path" in
      team_trb_all_players/*.py|team_trb_all_players/*.sh)
        git checkout "origin/$BRANCH" -- "$path" || return 1
        ;;
    esac
  done < <(git diff --name-only "$local_head" "$remote_head")
  git reset --soft "$remote_head" >/dev/null 2>&1 || return 1
  return 0
}

write_status "running" "Self-healing supervisor owns TREB local execution"
echo "[$(ts)] TREB self-healing supervisor started"

while true; do
  echo "[$(ts)] Launching unattended TREB runner"
  set +e
  bash "$RUNNER"
  RC=$?
  set -e

  if [[ $RC -eq 0 ]]; then
    write_status "complete" "TREB unattended runner completed successfully"
    git add "$STATUS" 2>/dev/null || true
    if ! git diff --cached --quiet; then
      git commit -m "Record TREB supervisor completion [skip ci]" || true
      git push origin "HEAD:$BRANCH" || true
    fi
    echo "[$(ts)] TREB_SELF_HEALING_COMPLETE=1"
    exit 0
  fi

  echo "[$(ts)] Unattended runner stopped rc=$RC; publishing diagnostics and waiting for remote fix"
  publish_failure "$RC"
  BASELINE="$(git rev-parse HEAD)"

  while true; do
    sleep "$POLL_SECONDS"
    if pull_remote_fix; then
      echo "[$(ts)] Remote fix detected and applied; automatically retrying from durable local cache"
      write_status "retrying" "Remote code fix detected; restarting unattended runner"
      break
    else
      CHECK_RC=$?
      if [[ $CHECK_RC -eq 2 ]]; then
        echo "[$(ts)] No remote fix yet; supervisor remains alive"
      else
        echo "[$(ts)] Remote update check encountered a recoverable problem; will retry"
      fi
    fi
  done
done
