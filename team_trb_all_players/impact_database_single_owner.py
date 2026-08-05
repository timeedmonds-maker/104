from __future__ import annotations

import json
import os
import re
import subprocess

import impact_database_supervisor as supervisor


_CORE_RE = re.compile(r"Core player/team on-off layer:\s*\*\*(\d+)/")
_ORIGINAL_STATUS_BODY = supervisor.status_body
_ORIGINAL_SEND_STATUS = supervisor._send_status_sync


def _execution_name() -> str:
    if os.getenv("CODESPACE_NAME"):
        return "GitHub Codespaces"
    if os.getenv("GITHUB_ACTIONS") == "true":
        return "GitHub Actions"
    return "local shell"


def status_body(stage: str, current_complete: int, worker_count: int, active: list[str], note: str = "") -> str:
    body = _ORIGINAL_STATUS_BODY(stage, current_complete, worker_count, active, note)
    marker = f"Execution: **{_execution_name()}**. "
    return body.replace("Current stage:", marker + "Current stage:", 1)


def _core_count(body: str) -> int | None:
    match = _CORE_RE.search(body or "")
    return int(match.group(1)) if match else None


def _current_public_body() -> str:
    command = [
        "gh", "api",
        f"repos/timeedmonds-maker/104/issues/comments/{supervisor.base.STATUS_COMMENT_ID}",
        "--jq", ".body",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=supervisor.base.REPO,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def send_status_monotonic(body: str) -> None:
    proposed = _core_count(body)
    current_body = _current_public_body()
    current = _core_count(current_body)

    # Never allow a stale or duplicate runner to move the public checkpoint
    # backwards. This makes Issue #9 monotonic even during cleanup of old runs.
    if proposed is not None and current is not None and proposed < current:
        print(
            f"[{supervisor.base.now()}] Refusing stale status regression: "
            f"proposed={proposed}, public={current}",
            flush=True,
        )
        return

    _ORIGINAL_SEND_STATUS(body)


supervisor.status_body = status_body
supervisor._send_status_sync = send_status_monotonic
