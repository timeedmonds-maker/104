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

remote_advanced() {
  git fetch origin "$BRANCH" >/dev/null 2>&1 || return 1
  local local_head remote_head
  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "origin/$BRANCH")"
  [[ "$remote_head" != "$local_head" ]]
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

  echo "[$(ts)] Fast-forward pull is currently blocked by local state; preserving all local data and retrying later"
  return 1
}

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

  # If automation already pushed a fix while the runner was failing, consume it first.
  # This avoids creating a local diagnostic commit on an obsolete parent.
  if remote_advanced; then
    if pull_remote_fix; then
      echo "[$(ts)] Remote fix was already waiting at failure time; skipped diagnostic commit to avoid divergence"
      return 10
    fi
  fi

  git add "$FAIL_REPORT" "$STATUS" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    local pre_commit_head
    pre_commit_head="$(git rev-parse HEAD)"
    git commit -m "Publish TREB failure diagnostics [skip ci]" || true
    if ! git push origin "HEAD:$BRANCH"; then
      echo "[$(ts)] Diagnostic push raced with a remote update; removing only the unpushed diagnostic commit"
      git reset --mixed "$pre_commit_head" || true
      git fetch origin "$BRANCH" || true
      if pull_remote_fix; then
        echo "[$(ts)] Remote fix applied after diagnostic push race"
        return 10
      fi
      echo "[$(ts)] Could not integrate remote update yet; preserving local diagnostic files and retrying later"
    fi
  fi
  return 0
}

stop_runner_group() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  echo "[$(ts)] Stopping active runner process group pid=$pid so a remote code fix can be applied"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  echo "[$(ts)] Runner did not stop after TERM; escalating to KILL"
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

write_status "running" "Self-healing supervisor owns TREB local execution"
echo "[$(ts)] TREB self-healing supervisor started"

while true; do
  echo "[$(ts)] Launching unattended TREB runner"
  set +e
  # Own process group lets the supervisor safely stop the runner and all descendants
  # when autonomous repair code arrives on the branch.
  setsid bash "$RUNNER" &
  RUNNER_PID=$!
  REMOTE_FIX_APPLIED=0

  while kill -0 "$RUNNER_PID" 2>/dev/null; do
    sleep "$POLL_SECONDS"
    if remote_advanced; then
      local_head="$(git rev-parse HEAD)"
      remote_head="$(git rev-parse "origin/$BRANCH")"
      echo "[$(ts)] Remote fix detected while runner is active: $local_head -> $remote_head"
      write_status "applying_remote_fix" "Remote branch advanced while runner active; safely restarting from durable cache"
      stop_runner_group "$RUNNER_PID"
      wait "$RUNNER_PID" 2>/dev/null || true
      if pull_remote_fix; then
        echo "[$(ts)] Remote fix applied during active run; automatically restarting from durable local cache"
        REMOTE_FIX_APPLIED=1
        break
      fi
      echo "[$(ts)] Remote fix detected but pull is temporarily blocked; runner remains stopped to protect state"
      break
    fi
  done

  if [[ $REMOTE_FIX_APPLIED -eq 1 ]]; then
    set -e
    write_status "retrying" "Remote code fix detected during active run; restarting unattended runner"
    continue
  fi

  wait "$RUNNER_PID"
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
  PUB_RC=$?
  if [[ $PUB_RC -eq 10 ]]; then
    echo "[$(ts)] Remote fix detected and applied at failure boundary; retrying automatically"
    write_status "retrying" "Remote code fix detected; restarting unattended runner"
    continue
  fi

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
