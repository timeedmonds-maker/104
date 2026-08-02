from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path(os.environ["PBPSTATS_OUTPUT_DIR"])
RUNTIME_LIMIT = int(os.getenv("PBPSTATS_RUNTIME_LIMIT_SECONDS", "650"))
SCRIPT = Path(__file__).with_name("full_adaptive_batch.py")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    error = ""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=False,
            timeout=RUNTIME_LIMIT,
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        error = f"Timed out after {RUNTIME_LIMIT} seconds: {exc}"
    except Exception as exc:  # keep the artifact step alive for diagnosis/retry
        error = repr(exc)

    complete_marker = OUTPUT_DIR / "batch_complete.json"
    completed = bool(exit_code == 0 and complete_marker.exists())
    payload = {
        "generated_at_utc": now(),
        "season": os.getenv("PBPSTATS_SEASON"),
        "team_ids": os.getenv("PBPSTATS_TEAM_IDS"),
        "group": os.getenv("PBPSTATS_GROUP"),
        "runtime_limit_seconds": RUNTIME_LIMIT,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timed_out": timed_out,
        "exit_code": exit_code,
        "completed": completed,
        "error": error,
    }
    (OUTPUT_DIR / "runner_result.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, sort_keys=True), flush=True)

    # A failed or timed-out team-season is intentionally retried by a later
    # checkpoint run. Keep this wrapper successful so the collector always runs.
    return 0


if __name__ == "__main__":
    sys.exit(main())
