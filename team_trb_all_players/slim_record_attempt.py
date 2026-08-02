from __future__ import annotations

# This file is watched by the production workflow so a normal main-branch
# update can force a registered single-job checkpoint cycle.

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("team_trb_all_players/retry_state")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", required=True)
    parser.add_argument("--team-id", required=True, type=int)
    parser.add_argument("--completed", required=True)
    parser.add_argument("--timed-out", required=True)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--runner-result", default="")
    args = parser.parse_args()

    root = Path(args.root)
    path = root / args.season / f"{args.team_id}.json"
    completed = parse_bool(args.completed)
    timed_out = parse_bool(args.timed_out)

    if completed:
        path.unlink(missing_ok=True)
        return

    previous = read_json(path)
    attempts = max(0, int(previous.get("attempts", 0))) + 1
    runner_result: dict[str, Any] = {}
    if args.runner_result:
        runner_result = read_json(Path(args.runner_result))

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "season": args.season,
        "team_id": args.team_id,
        "attempts": attempts,
        "updated_at_utc": now(),
        "timed_out": timed_out,
        "last_exit_code": runner_result.get("exit_code"),
        "last_elapsed_seconds": runner_result.get("elapsed_seconds"),
        "last_error": runner_result.get("error", ""),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
