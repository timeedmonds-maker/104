from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import impact_database_build_fixed as fixed


base = fixed.base
HEARTBEAT_SECONDS = max(30, int(os.getenv("IMPACT_DB_HEARTBEAT_SECONDS", "120")))
WATCHDOG_SECONDS = max(300, int(os.getenv("IMPACT_DB_WATCHDOG_SECONDS", "1200")))
_STATUS_QUEUE: queue.Queue[str] = queue.Queue(maxsize=1)
_STATUS_SEND_LOCK = threading.Lock()


def resilient_git_commit_progress(label: str) -> bool:
    """Checkpoint progress without allowing a transient or hung Git call to stop the build."""
    committed = False
    try:
        subprocess.run(
            ["git", "add", "-A", "--", str(base.DB_ROOT)],
            cwd=base.REPO,
            check=True,
            timeout=120,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=base.REPO,
            timeout=60,
        )
        if diff.returncode not in (0, 1):
            raise subprocess.CalledProcessError(diff.returncode, diff.args)
        if diff.returncode == 1:
            subprocess.run(
                ["git", "commit", "-m", f"Impact database progress: {label}"],
                cwd=base.REPO,
                check=True,
                timeout=120,
            )
            committed = True

        # Always retry synchronization. This also pushes a checkpoint commit
        # left locally by an earlier transient pull/push failure.
        subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=base.REPO,
            check=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=base.REPO,
            check=True,
            timeout=120,
        )
        return committed
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"[{base.now()}] Git checkpoint synchronization failed or timed out; "
            f"the build will continue and retry later: {exc}",
            flush=True,
        )
        return False


def _send_status_sync(body: str) -> None:
    """Send one GitHub status update with a hard timeout."""
    command = [
        "gh", "api", "--method", "PATCH",
        f"repos/timeedmonds-maker/104/issues/comments/{base.STATUS_COMMENT_ID}",
        "-f", f"body={body}",
    ]
    try:
        with _STATUS_SEND_LOCK:
            subprocess.run(
                command,
                cwd=base.REPO,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=30,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[{base.now()}] Status update failed or timed out: {exc}", flush=True)


def _status_worker() -> None:
    """Publish queued status updates without blocking task scheduling."""
    while True:
        body = _STATUS_QUEUE.get()
        try:
            _send_status_sync(body)
        finally:
            _STATUS_QUEUE.task_done()


def resilient_update_status(body: str) -> None:
    """Queue the newest status without blocking the collector."""
    try:
        _STATUS_QUEUE.put_nowait(body)
        return
    except queue.Full:
        pass

    # Keep only the newest waiting status. A status already being sent is left
    # alone, while any older queued update is replaced.
    try:
        _STATUS_QUEUE.get_nowait()
        _STATUS_QUEUE.task_done()
    except queue.Empty:
        pass

    try:
        _STATUS_QUEUE.put_nowait(body)
    except queue.Full:
        # The worker raced us and another newer update is already queued.
        pass


def status_body(stage: str, current_complete: int, worker_count: int, active: list[str], note: str = "") -> str:
    other_complete = base.completed_count("pair" if stage == "core" else "core")
    core_complete = current_complete if stage == "core" else other_complete
    pair_complete = other_complete if stage == "core" else current_complete
    active_text = ", ".join(active) if active else "none"
    suffix = f"\n\n{note}" if note else ""
    return (
        "**NBA historical player-impact database build**\n\n"
        f"Core player/team on-off layer: **{core_complete}/{base.EXPECTED_TEAM_SEASONS}** team-seasons.\n\n"
        f"Teammate interaction layer: **{pair_complete}/{base.EXPECTED_TEAM_SEASONS}** team-seasons.\n\n"
        f"Current stage: **{stage}**. Workers: {worker_count}. Active: {active_text}. "
        f"Updated: {base.now()}.{suffix}"
    )


def resilient_run_stage(stage: str) -> None:
    """
    Keep workers continuously occupied rather than waiting for a fixed batch.

    The original runner submitted exactly ``worker_count`` tasks and waited for
    every task in that batch to finish before submitting more. One slow API-heavy
    team-season therefore left the other worker idle and froze the public status.
    This queue replaces that batch barrier, publishes heartbeats, and hard-exits
    for the shell supervisor to restart when *no* team-season finishes for an
    extended period.
    """
    worker_count = base.CORE_WORKERS if stage == "core" else base.PAIR_WORKERS
    commit_every = base.CORE_COMMIT_EVERY if stage == "core" else base.PAIR_COMMIT_EVERY
    function = base.fetch_core_task if stage == "core" else base.fetch_pair_task
    pending = deque(base.all_tasks(stage))
    completed_since_commit = 0
    prior_complete = base.completed_count(stage)
    no_gain_results = 0

    if not pending:
        base.git_commit_progress(f"{stage} complete")
        return

    executor = ThreadPoolExecutor(max_workers=worker_count)
    inflight: dict[object, tuple[str, int, int]] = {}

    def fill_workers() -> None:
        while pending and len(inflight) < worker_count:
            season, team_id, attempts = pending.popleft()
            future = executor.submit(function, season, team_id)
            inflight[future] = (season, team_id, attempts)
            print(
                f"[{base.now()}] {stage.upper()} {prior_complete}/{base.EXPECTED_TEAM_SEASONS}; "
                f"starting {season}-{team_id}",
                flush=True,
            )

    fill_workers()
    last_completion = time.monotonic()

    try:
        while inflight:
            done, _ = wait(
                list(inflight),
                timeout=HEARTBEAT_SECONDS,
                return_when=FIRST_COMPLETED,
            )

            if not done:
                active = [f"{season}-{team_id}" for season, team_id, _ in inflight.values()]
                elapsed = int(time.monotonic() - last_completion)
                print(
                    f"[{base.now()}] {stage.upper()} heartbeat: {prior_complete}/"
                    f"{base.EXPECTED_TEAM_SEASONS}; active={', '.join(active)}; "
                    f"seconds_since_completion={elapsed}",
                    flush=True,
                )
                base.update_status(status_body(
                    stage,
                    prior_complete,
                    worker_count,
                    active,
                    note=f"Heartbeat: workers are still running; {elapsed}s since the last team-season completed.",
                ))
                if elapsed >= WATCHDOG_SECONDS:
                    print(
                        f"[{base.now()}] No {stage} team-season completed for {elapsed}s; "
                        "saving checkpoints and forcing a supervised restart.",
                        flush=True,
                    )
                    base.git_commit_progress(f"{stage} watchdog checkpoint")
                    _send_status_sync(status_body(
                        stage,
                        prior_complete,
                        worker_count,
                        active,
                        note="Watchdog restart triggered after prolonged lack of completed work.",
                    ))
                    sys.stdout.flush()
                    sys.stderr.flush()
                    os._exit(75)
                continue

            for future in done:
                season, team_id, attempts = inflight.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "complete": False,
                        "season": season,
                        "team_id": team_id,
                        "error": repr(exc),
                    }

                print(
                    f"[{base.now()}] {stage.upper()} {season}-{team_id} "
                    f"complete={result.get('complete')} error={result.get('error', '')}",
                    flush=True,
                )
                last_completion = time.monotonic()

                current_complete = base.completed_count(stage)
                gained = current_complete - prior_complete
                completed_since_commit += max(0, gained)
                if gained > 0:
                    no_gain_results = 0
                else:
                    no_gain_results += 1
                prior_complete = current_complete

                if not result.get("complete"):
                    pending.append((season, team_id, attempts + 1))

                # Refill the free worker immediately. Git synchronization and
                # public status reporting must never sit in the scheduling path.
                fill_workers()

                if completed_since_commit >= commit_every or current_complete == base.EXPECTED_TEAM_SEASONS:
                    base.git_commit_progress(f"{stage} {current_complete} of {base.EXPECTED_TEAM_SEASONS}")
                    completed_since_commit = 0

                active = [f"{s}-{t}" for s, t, _ in inflight.values()]
                base.update_status(status_body(stage, current_complete, worker_count, active))

                if no_gain_results >= max(8, worker_count * 4):
                    print(
                        f"[{base.now()}] Repeated {stage} retries without a new completion; "
                        f"pausing {base.STALL_PAUSE}s before continuing.",
                        flush=True,
                    )
                    base.git_commit_progress(f"{stage} retry state")
                    time.sleep(base.STALL_PAUSE)
                    no_gain_results = 0

        base.git_commit_progress(f"{stage} complete")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


threading.Thread(
    target=_status_worker,
    name="impact-status-updater",
    daemon=True,
).start()

# Install all resilient orchestration hooks into the original module. The
# corrected collector remains unchanged; only scheduling, status and Git sync
# behaviour are replaced.
base.git_commit_progress = resilient_git_commit_progress
fixed.git_commit_progress = resilient_git_commit_progress
base.update_status = resilient_update_status
base.run_stage = resilient_run_stage


if __name__ == "__main__":
    raise SystemExit(fixed.main())
