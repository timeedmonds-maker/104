from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPAIR_DIR = BASE / "impact_database" / "corrected_off_wowy"
SUMMARY = REPAIR_DIR / "wowy_collection_summary.json"
COLLECTOR = BASE / "wowy_repair_collector.py"
TOTAL = 5726


def state() -> tuple[int, int, bool]:
    if not SUMMARY.exists():
        return 0, TOTAL, False
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    return (
        int(data.get("complete_windows") or 0),
        int(data.get("remaining_windows") or TOTAL),
        bool(data.get("all_complete")),
    )


def checkpoint() -> None:
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(REPAIR_DIR.relative_to(BASE.parent))], check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", "Advance exact-date WOWY repair [skip ci]"], check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "treb-stage2-actions-live"], check=True)
    subprocess.run(["git", "push", "origin", "HEAD:treb-stage2-actions-live"], check=True)


def main() -> None:
    # Leave enough headroom under the known finalizer job's 90-minute timeout.
    deadline = time.monotonic() + 70 * 60
    complete, remaining, done = state()
    print(f"WOWY_REPAIR_RESUME complete={complete}/{TOTAL} remaining={remaining}", flush=True)
    if done:
        print("WOWY_REPAIR_COMPLETE=1", flush=True)
        return

    stalls = 0
    iteration = 0
    while time.monotonic() < deadline:
        iteration += 1
        before = complete
        print(f"WOWY_REPAIR_ITERATION={iteration} before={before}", flush=True)
        proc = subprocess.run([
            sys.executable, str(COLLECTOR),
            "--batch-size", "25",
            "--attempts", "5",
            "--interval", "0.5",
        ], check=False)

        # Preserve successful rows even when another window in the batch errors.
        checkpoint()
        complete, remaining, done = state()
        print(
            f"WOWY_REPAIR_CHECKPOINT complete={complete}/{TOTAL} remaining={remaining} collector_rc={proc.returncode}",
            flush=True,
        )
        if done:
            print("WOWY_REPAIR_COMPLETE=1", flush=True)
            return

        if complete <= before:
            stalls += 1
            print(f"WOWY_REPAIR_NO_PROGRESS stalls={stalls}", flush=True)
            if stalls >= 3:
                raise RuntimeError("WOWY repair made no durable progress for three consecutive batches")
            time.sleep(15)
        else:
            stalls = 0

    raise RuntimeError(
        f"WOWY repair runner handoff required: complete={complete}/{TOTAL}, remaining={remaining}; durable progress committed"
    )


if __name__ == "__main__":
    main()
