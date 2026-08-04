from __future__ import annotations

import json
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
RUN_SECONDS = max(600, int(os.getenv("IMPACT_DB_RUN_SECONDS", "15000")))
_STATUS_QUEUE: queue.Queue[str] = queue.Queue(maxsize=1)
_STATUS_SEND_LOCK = threading.Lock()


def run_command(command: list[str], timeout: int, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=base.REPO,
        check=check,
        timeout=timeout,
        text=True,
    )


def resilient_git_commit_progress(label: str) -> bool:
    """Commit and push database checkpoints without stopping collection on Git trouble."""
    committed = False
    try:
        run_command(["git", "add", "-A", "--", str(base.DB_ROOT)], timeout=120)
        diff = run_command(["git", "diff", "--cached", "--quiet"], timeout=60, check=False)
        if diff.returncode not in (0, 1):
            raise subprocess.CalledProcessError(diff.returncode, diff.args)
        if diff.returncode == 1:
            run_command(
                ["git", "commit", "-m", f"Impact database progress: {label}"],
                timeout=120,
            )
            committed = True
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[{base.now()}] Could not create checkpoint commit: {exc}", flush=True)
        return False

    for attempt in range(1, 6):
        try:
            run_command(["git", "pull", "--rebase", "origin", "main"], timeout=180)
            run_command(["git", "push", "origin", "HEAD:main"], timeout=180)
            return committed
        except (OSError, subprocess.SubprocessError) as exc:
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=base.REPO,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(
                f"[{base.now()}] Checkpoint sync attempt {attempt}/5 failed: {exc}",
                flush=True,
            )
            if attempt < 5:
                time.sleep(10 * attempt)

    print(
        f"[{base.now()}] Checkpoint remains local after repeated push failures; "
        "the workflow cleanup step will retry.",
        flush=True,
    )
    return committed


def _send_status_sync(body: str) -> None:
    command = [
        "gh",
        "api",
        "--method",
        "PATCH",
        f"repos/timeedmonds-maker/104/issues/comments/{base.STATUS_COMMENT_ID}",
        "-f",
        f"body={body}",
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
    while True:
        body = _STATUS_QUEUE.get()
        try:
            _send_status_sync(body)
        finally:
            _STATUS_QUEUE.task_done()


def resilient_update_status(body: str) -> None:
    try:
        _STATUS_QUEUE.put_nowait(body)
        return
    except queue.Full:
        pass

    try:
        _STATUS_QUEUE.get_nowait()
        _STATUS_QUEUE.task_done()
    except queue.Empty:
        pass

    try:
        _STATUS_QUEUE.put_nowait(body)
    except queue.Full:
        pass


def status_body(current_complete: int, active: list[str], note: str = "") -> str:
    active_text = ", ".join(active) if active else "none"
    suffix = f"\n\n{note}" if note else ""
    return (
        "**NBA historical player-impact database build**\n\n"
        f"Core player/team on-off layer: **{current_complete}/{base.EXPECTED_TEAM_SEASONS}** team-seasons.\n\n"
        "Teammate interaction layer: **disabled — not part of this build**.\n\n"
        f"Execution: **GitHub Actions**. Workers: {base.CORE_WORKERS}. Active: {active_text}. "
        f"Updated: {base.now()}.{suffix}"
    )


def bounded_core_chunk() -> int:
    """Run core tasks until the soft time budget is reached, then checkpoint and return."""
    worker_count = base.CORE_WORKERS
    commit_every = base.CORE_COMMIT_EVERY
    pending = deque(base.all_tasks("core"))
    completed_since_commit = 0
    prior_complete = base.completed_count("core")
    no_gain_results = 0
    deadline = time.monotonic() + RUN_SECONDS
    draining = False

    if not pending:
        resilient_git_commit_progress("core complete")
        return 0

    executor = ThreadPoolExecutor(max_workers=worker_count)
    inflight: dict[object, tuple[str, int, int]] = {}

    def begin_draining(reason: str) -> None:
        nonlocal draining
        if draining:
            return
        draining = True
        active = [f"{season}-{team_id}" for season, team_id, _ in inflight.values()]
        print(f"[{base.now()}] Entering checkpoint drain: {reason}", flush=True)
        resilient_update_status(
            status_body(
                prior_complete,
                active,
                note="This Actions chunk reached its safe time limit. Current tasks are finishing before an automatic continuation.",
            )
        )

    def fill_workers() -> None:
        if draining:
            return
        while pending and len(inflight) < worker_count:
            season, team_id, attempts = pending.popleft()
            future = executor.submit(base.fetch_core_task, season, team_id)
            inflight[future] = (season, team_id, attempts)
            print(
                f"[{base.now()}] CORE {prior_complete}/{base.EXPECTED_TEAM_SEASONS}; "
                f"starting {season}-{team_id}",
                flush=True,
            )

    fill_workers()
    last_completion = time.monotonic()

    try:
        while inflight:
            if time.monotonic() >= deadline:
                begin_draining("soft runtime limit reached")

            wait_timeout = HEARTBEAT_SECONDS
            if not draining:
                wait_timeout = max(1, min(HEARTBEAT_SECONDS, int(deadline - time.monotonic()) + 1))

            done, _ = wait(
                list(inflight),
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )

            if not done:
                if time.monotonic() >= deadline:
                    begin_draining("soft runtime limit reached")
                active = [f"{season}-{team_id}" for season, team_id, _ in inflight.values()]
                elapsed = int(time.monotonic() - last_completion)
                print(
                    f"[{base.now()}] CORE heartbeat: {prior_complete}/{base.EXPECTED_TEAM_SEASONS}; "
                    f"active={', '.join(active)}; seconds_since_completion={elapsed}",
                    flush=True,
                )
                resilient_update_status(
                    status_body(
                        prior_complete,
                        active,
                        note=f"Heartbeat: workers are still running; {elapsed}s since the last team-season completed.",
                    )
                )
                if elapsed >= WATCHDOG_SECONDS:
                    print(
                        f"[{base.now()}] No core team-season completed for {elapsed}s; "
                        "saving checkpoints and ending this chunk for automatic continuation.",
                        flush=True,
                    )
                    resilient_git_commit_progress("core watchdog checkpoint")
                    _send_status_sync(
                        status_body(
                            prior_complete,
                            active,
                            note="Watchdog ended this Actions chunk after prolonged lack of completed work. A continuation will start automatically.",
                        )
                    )
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
                    f"[{base.now()}] CORE {season}-{team_id} "
                    f"complete={result.get('complete')} error={result.get('error', '')}",
                    flush=True,
                )
                last_completion = time.monotonic()

                current_complete = base.completed_count("core")
                gained = current_complete - prior_complete
                completed_since_commit += max(0, gained)
                if gained > 0:
                    no_gain_results = 0
                else:
                    no_gain_results += 1
                prior_complete = current_complete

                if not result.get("complete") and not draining:
                    pending.append((season, team_id, attempts + 1))

                if time.monotonic() >= deadline:
                    begin_draining("soft runtime limit reached")
                fill_workers()

                if completed_since_commit >= commit_every or current_complete == base.EXPECTED_TEAM_SEASONS:
                    resilient_git_commit_progress(
                        f"core {current_complete} of {base.EXPECTED_TEAM_SEASONS}"
                    )
                    completed_since_commit = 0

                active = [f"{s}-{t}" for s, t, _ in inflight.values()]
                resilient_update_status(status_body(current_complete, active))

                if not draining and no_gain_results >= max(8, worker_count * 4):
                    print(
                        f"[{base.now()}] Repeated retries without a new completion; "
                        f"pausing {base.STALL_PAUSE}s before continuing.",
                        flush=True,
                    )
                    resilient_git_commit_progress("core retry state")
                    time.sleep(base.STALL_PAUSE)
                    no_gain_results = 0

        current_complete = base.completed_count("core")
        label = (
            "core complete"
            if current_complete == base.EXPECTED_TEAM_SEASONS
            else f"core Actions chunk {current_complete} of {base.EXPECTED_TEAM_SEASONS}"
        )
        resilient_git_commit_progress(label)
        return 0
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def main() -> int:
    for directory in (base.CORE_ROOT, base.PAIR_ROOT, base.OUTPUT_ROOT):
        directory.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"],
        cwd=base.REPO,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        cwd=base.REPO,
        check=True,
    )

    base.git_commit_progress = resilient_git_commit_progress
    fixed.git_commit_progress = resilient_git_commit_progress
    base.update_status = resilient_update_status

    threading.Thread(
        target=_status_worker,
        name="impact-actions-status-updater",
        daemon=True,
    ).start()

    before = base.completed_count("core")
    resilient_update_status(
        status_body(
            before,
            [],
            note="A bounded GitHub Actions build chunk has started. It will checkpoint and continue automatically until the core database is complete.",
        )
    )
    print(
        f"[{base.now()}] GitHub Actions core chunk started at {before}/"
        f"{base.EXPECTED_TEAM_SEASONS}; run_seconds={RUN_SECONDS}; workers={base.CORE_WORKERS}",
        flush=True,
    )

    bounded_core_chunk()
    after = base.completed_count("core")

    if after == base.EXPECTED_TEAM_SEASONS:
        print(f"[{base.now()}] Core collection complete; aggregating outputs.", flush=True)
        manifest = base.aggregate_outputs()
        resilient_git_commit_progress("core aggregate outputs")
        _send_status_sync(
            status_body(
                after,
                [],
                note=(
                    "Core collection and aggregated outputs are complete. "
                    "The teammate interaction layer remains intentionally disabled."
                ),
            )
        )
        print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    else:
        resilient_git_commit_progress(
            f"core Actions chunk finished {after} of {base.EXPECTED_TEAM_SEASONS}"
        )
        _send_status_sync(
            status_body(
                after,
                [],
                note="This Actions chunk finished safely. The workflow will dispatch the next continuation automatically.",
            )
        )

    print(
        json.dumps(
            {
                "core_before": before,
                "core_after": after,
                "expected": base.EXPECTED_TEAM_SEASONS,
                "complete": after == base.EXPECTED_TEAM_SEASONS,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
