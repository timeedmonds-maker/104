from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
CHECKPOINT_ROOT = BASE / "checkpoints"
RETRY_ROOT = BASE / "retry_state"
FINAL_ROOT = BASE / "full_career_output"
WORK_ROOT = BASE / "codespace_work"
STATUS_COMMENT_ID = os.getenv("TEAM_TRB_STATUS_COMMENT_ID", "5161464311")
WORKERS = max(1, min(4, int(os.getenv("TEAM_TRB_WORKERS", "2"))))
TASK_RUNTIME_SECONDS = max(300, int(os.getenv("TEAM_TRB_TASK_RUNTIME_SECONDS", "1800")))
BATCH_PAUSE_SECONDS = max(0.0, float(os.getenv("TEAM_TRB_BATCH_PAUSE_SECONDS", "2")))

os.environ["PBPSTATS_CHECKPOINT_ROOT"] = str(CHECKPOINT_ROOT)
os.environ["PBPSTATS_RETRY_ROOT"] = str(RETRY_ROOT)
os.environ["PBPSTATS_FINAL_MARKER"] = str(FINAL_ROOT / "aggregate_complete.json")
sys.path.insert(0, str(BASE))

import slim_checkpoint_plan as planner  # noqa: E402


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def pending_tasks() -> tuple[list[dict[str, Any]], int]:
    tasks: list[dict[str, Any]] = []
    complete_count = 0
    for start_year in range(2000, 2026):
        season = f"{start_year}-{str(start_year + 1)[-2:]}"
        from_date, to_date = planner.season_dates(start_year)
        for team_id in planner.TEAM_IDS:
            if planner.checkpoint_complete(season, team_id):
                complete_count += 1
                continue
            tasks.append({
                "season": season,
                "from_date": from_date,
                "to_date": to_date,
                "team_id": team_id,
                "task_key": f"{season}-{team_id}",
                "attempts": planner.retry_attempts(season, team_id),
            })
    tasks.sort(key=lambda row: (row["attempts"], row["season"], row["team_id"]))
    return tasks, complete_count


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    season = str(task["season"])
    team_id = int(task["team_id"])
    output_dir = WORK_ROOT / season / str(team_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("GITHUB_OUTPUT", None)
    env.update({
        "PBPSTATS_SEASON": season,
        "PBPSTATS_FROM_DATE": str(task["from_date"]),
        "PBPSTATS_TO_DATE": str(task["to_date"]),
        "PBPSTATS_GROUP": str(team_id),
        "PBPSTATS_TEAM_IDS": str(team_id),
        "PBPSTATS_TOP_WINDOW_DAYS": "14",
        "PBPSTATS_CONNECT_TIMEOUT": "10",
        "PBPSTATS_READ_TIMEOUT": "35",
        "PBPSTATS_REQUEST_PAUSE": "0.55",
        "PBPSTATS_RUNTIME_LIMIT_SECONDS": str(TASK_RUNTIME_SECONDS),
        "PBPSTATS_OUTPUT_DIR": str(output_dir),
    })
    print(f"[{now()}] START {task['task_key']} prior_attempts={task['attempts']}", flush=True)
    completed_process = subprocess.run(
        [sys.executable, str(BASE / "slim_run_one.py")],
        cwd=REPO,
        env=env,
        check=False,
    )
    result = read_json(output_dir / "runner_result.json")
    result.update({
        "task_key": task["task_key"],
        "season": season,
        "team_id": team_id,
        "wrapper_exit_code": completed_process.returncode,
        "output_dir": str(output_dir),
    })
    print(
        f"[{now()}] END {task['task_key']} completed={result.get('completed')} "
        f"timed_out={result.get('timed_out')} elapsed={result.get('elapsed_seconds')}",
        flush=True,
    )
    return result


def collect_and_record(tasks: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    subprocess.run(
        [sys.executable, str(BASE / "slim_collect.py"), str(WORK_ROOT), str(CHECKPOINT_ROOT)],
        cwd=REPO,
        check=True,
    )
    by_key = {str(result.get("task_key")): result for result in results}
    for task in tasks:
        result = by_key.get(str(task["task_key"]), {})
        output_dir = WORK_ROOT / str(task["season"]) / str(task["team_id"])
        subprocess.run(
            [
                sys.executable,
                str(BASE / "slim_record_attempt.py"),
                "--season", str(task["season"]),
                "--team-id", str(task["team_id"]),
                "--completed", "true" if result.get("completed") is True else "false",
                "--timed-out", "true" if result.get("timed_out") is True else "false",
                "--root", str(RETRY_ROOT),
                "--runner-result", str(output_dir / "runner_result.json"),
            ],
            cwd=REPO,
            check=True,
        )
        if result.get("completed") is True and planner.checkpoint_complete(
            str(task["season"]), int(task["team_id"])
        ):
            shutil.rmtree(output_dir, ignore_errors=True)


def git_commit_progress(label: str) -> bool:
    paths = [str(CHECKPOINT_ROOT), str(RETRY_ROOT), str(FINAL_ROOT)]
    subprocess.run(["git", "add", "-A", "--", *paths], cwd=REPO, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if diff.returncode == 0:
        print(f"[{now()}] No repository progress to commit.", flush=True)
        return False
    subprocess.run(
        ["git", "commit", "-m", f"Codespace team rebound progress: {label}"],
        cwd=REPO,
        check=True,
    )
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=REPO, check=True)
    print(f"[{now()}] Progress committed and pushed.", flush=True)
    return True


def update_status(body: str) -> None:
    command = [
        "gh", "api", "--method", "PATCH",
        f"repos/timeedmonds-maker/104/issues/comments/{STATUS_COMMENT_ID}",
        "-f", f"body={body}",
    ]
    try:
        subprocess.run(command, cwd=REPO, check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[{now()}] Status comment update failed: {exc}", flush=True)


def aggregate_final() -> None:
    subprocess.run(
        [sys.executable, str(BASE / "slim_checkpoint_aggregate.py"), str(CHECKPOINT_ROOT), str(FINAL_ROOT)],
        cwd=REPO,
        check=True,
    )
    git_commit_progress("complete career leaderboard")


def main() -> int:
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)
    RETRY_ROOT.mkdir(parents=True, exist_ok=True)
    FINAL_ROOT.mkdir(parents=True, exist_ok=True)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "config", "user.name", "github-codespaces[bot]"], cwd=REPO, check=True)
    subprocess.run(
        ["git", "config", "user.email", "codespaces@users.noreply.github.com"],
        cwd=REPO,
        check=True,
    )

    print(
        f"[{now()}] Codespaces executor started with {WORKERS} workers. "
        "Validated checkpoints are committed after every batch.",
        flush=True,
    )

    while True:
        tasks, complete_before = pending_tasks()
        if not tasks:
            if planner.final_complete():
                body = (
                    "**Codespaces executor complete**\n\n"
                    "Progress: **780/780** validated team-seasons.\n\n"
                    "The final career team total rebound percentage dataset has been aggregated and committed."
                )
                update_status(body)
                print(f"[{now()}] Dataset already complete.", flush=True)
                return 0
            print(f"[{now()}] All checkpoints present; building final leaderboard.", flush=True)
            aggregate_final()
            continue

        batch = tasks[:WORKERS]
        print(
            f"[{now()}] Progress {complete_before}/780. Starting batch: "
            + ", ".join(task["task_key"] for task in batch),
            flush=True,
        )
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {executor.submit(run_task, task): task for task in batch}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    print(f"[{now()}] TASK ERROR {task['task_key']}: {exc!r}", flush=True)
                    results.append({
                        "task_key": task["task_key"],
                        "season": task["season"],
                        "team_id": task["team_id"],
                        "completed": False,
                        "timed_out": False,
                        "error": repr(exc),
                    })

        collect_and_record(batch, results)
        remaining, complete_after = pending_tasks()
        git_commit_progress(", ".join(task["task_key"] for task in batch))
        completed_keys = [
            str(result.get("task_key"))
            for result in results
            if result.get("completed") is True
        ]
        incomplete_keys = [
            str(result.get("task_key"))
            for result in results
            if result.get("completed") is not True
        ]
        body = (
            "**Codespaces team rebound executor**\n\n"
            f"Progress: **{complete_after}/780** validated team-seasons.\n\n"
            f"Latest validated: {', '.join(completed_keys) or 'none'}.\n\n"
            f"Preserved for retry: {', '.join(incomplete_keys) or 'none'}.\n\n"
            f"Pending: {len(remaining)}. Workers: {WORKERS}. Updated: {now()}."
        )
        update_status(body)
        print(
            f"[{now()}] Batch complete. Progress {complete_after}/780; "
            f"pending={len(remaining)}.",
            flush=True,
        )
        time.sleep(BATCH_PAUSE_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
