from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
RUN_ID = os.environ["GITHUB_RUN_ID"]
ISSUE = os.getenv("STATUS_ISSUE", "9")
STALE_RUNS = (30761334701, 30761334722, 30761493273)


def request(method: str, path: str, payload: dict[str, object] | None = None) -> None:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "team-trb-dataset-workflow",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        if method == "POST" and path.endswith("/cancel") and exc.code in (404, 409):
            return
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {exc.code} for {path}: {detail}") from exc


def comment(body: str) -> None:
    request("POST", f"repos/{REPOSITORY}/issues/{ISSUE}/comments", {"body": body})


def main() -> None:
    mode = sys.argv[1]
    if mode == "start":
        for stale_run in STALE_RUNS:
            request("POST", f"repos/{REPOSITORY}/actions/runs/{stale_run}/cancel")
        comment(f"Production extraction started: run {RUN_ID} on {os.getenv('RUNNER_OS', 'GitHub Actions')}.")
    elif mode == "planned":
        comment(f"Run {RUN_ID}: planning completed and the 156-shard matrix is ready.")
    elif mode == "final":
        status = os.getenv("FINAL_STATUS", "unknown")
        comment(f"Run {RUN_ID} reached the aggregate stage with status: {status}.")
    else:
        raise SystemExit(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
