from __future__ import annotations

import argparse
import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_corrected_off_batch_v2 as v2

batch = v2.batch
core = batch.core
QUEUE = core.OUT / "deferred_failure_queue.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def robust_stat_minutes(payload: dict[str, Any], player_id: str, player_name: str):
    candidates = core.rows(payload)
    exact = [r for r in candidates if core.clean_id(r.get("PlayerId") or r.get("EntityId") or r.get("PlayerID")) == player_id]
    if not exact:
        target = norm(player_name)
        exact = [r for r in candidates if norm(r.get("Name")) == target]
    if len(exact) != 1:
        return None, None, None
    row = exact[0]
    return core.finite(row.get("MinutesOn")), core.finite(row.get("MinutesOff")), row


# Preserve ID-first matching but make the name fallback accent-insensitive.
core.stat_minutes = robust_stat_minutes


def window_key(window: dict[str, Any]) -> str:
    return batch.window_cache_path(window).name


def load_queue() -> dict[str, dict[str, Any]]:
    if not QUEUE.exists():
        return {}
    try:
        raw = json.loads(QUEUE.read_text(encoding="utf-8"))
        items = raw.get("windows") if isinstance(raw, dict) else None
        return items if isinstance(items, dict) else {}
    except Exception:
        return {}


def save_queue(state: dict[str, dict[str, Any]]) -> None:
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    attempts: dict[str, int] = {}
    for value in state.values():
        n = int(value.get("attempts") or 0)
        attempts[str(n)] = attempts.get(str(n), 0) + 1
    QUEUE.write_text(json.dumps({
        "generated_utc": now(),
        "policy": "Fresh pending tenure windows are attempted once before failures are revisited. Deferred retries use lower concurrency to reduce repeated PBP Stats 503 pressure.",
        "window_count": len(state),
        "attempt_histogram": attempts,
        "windows": state,
    }, indent=2), encoding="utf-8")


def compact_error(window: dict[str, Any], error: dict[str, Any] | None, attempts: int) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "attempts": attempts,
        "last_attempt_utc": now(),
        "season": window.get("season"),
        "team_id": window.get("team_id"),
        "team_abbr": window.get("team_abbr"),
        "player_id": window.get("player_id"),
        "player_name": window.get("player_name") or window.get("player"),
        "query_start_date": window.get("query_start_date"),
        "query_end_date": window.get("query_end_date"),
    }
    if error:
        if error.get("error"):
            detail["last_error"] = str(error.get("error"))[:1000]
        result = error.get("result") if isinstance(error.get("result"), dict) else {}
        if result:
            detail["last_complete"] = bool(result.get("complete"))
            detail["last_metric_count"] = int(result.get("metric_count") or 0)
            requests = result.get("requests") if isinstance(result.get("requests"), dict) else {}
            detail["last_requests"] = requests
    return detail


def run(batch_size: int, workers: int = 4) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")

    windows = batch.impact_windows()
    batch.prepare_cache_index(windows)
    pending = [w for w in windows if not v2.cached_complete(w)]
    pending_by_key = {window_key(w): w for w in pending}

    state = load_queue()
    state = {k: v for k, v in state.items() if k in pending_by_key}

    fresh = [w for w in pending if window_key(w) not in state]
    deferred = [w for w in pending if window_key(w) in state]

    if fresh:
        mode = "fresh"
        selected = fresh[:batch_size]
        effective_workers = workers
    else:
        mode = "retry"
        deferred.sort(key=lambda w: (
            int(state.get(window_key(w), {}).get("attempts") or 0),
            str(state.get(window_key(w), {}).get("last_attempt_utc") or ""),
            window_key(w),
        ))
        selected = deferred[:max(10, min(batch_size // 2, 40))]
        effective_workers = min(workers, 2)

    errors: list[dict[str, Any]] = []
    successes = 0
    completed = 0

    if selected:
        with ThreadPoolExecutor(max_workers=effective_workers, thread_name_prefix="treb-off-v3") as pool:
            futures = {pool.submit(batch.collect_one, window): window for window in selected}
            for future in as_completed(futures):
                window = futures[future]
                try:
                    result, error = future.result()
                except Exception as exc:
                    result, error = {}, {"window": window, "error": repr(exc)}
                key = window_key(window)
                if error or not result.get("complete"):
                    previous = state.get(key, {})
                    attempts = int(previous.get("attempts") or 0) + 1
                    state[key] = compact_error(window, error, attempts)
                    errors.append(error or {"window": window, "error": "incomplete result"})
                else:
                    state.pop(key, None)
                    successes += 1
                completed += 1
                if completed % 25 == 0 or completed == len(selected):
                    print(
                        f"v3 {mode} {completed}/{len(selected)} workers={effective_workers} successes={successes} deferred_now={len(state)}",
                        flush=True,
                    )

    save_queue(state)
    summary = batch.assemble(windows)
    summary.update({
        "stage2_v3": True,
        "selection_mode": mode,
        "batch_requested": len(selected),
        "batch_successes": successes,
        "batch_errors": len(errors),
        "batch_workers": effective_workers,
        "fresh_pending_before": len(fresh),
        "deferred_pending_before": len(deferred),
        "deferred_queue_windows": len(state),
        "fresh_first_policy": True,
        "retry_workers": min(workers, 2),
        "batch_error_examples": errors[:10],
        "network_policy": "Full-team-schedule windows are reused from the completed core. Partial-tenure failures are quarantined after one attempt so fresh windows continue; deferred retries run later at lower concurrency.",
    })
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "complete_windows": summary.get("complete_windows"),
        "remaining_windows": summary.get("remaining_windows"),
        "selection_mode": mode,
        "batch_successes": successes,
        "batch_errors": len(errors),
        "fresh_pending_before": len(fresh),
        "deferred_queue_windows": len(state),
    }, indent=2), flush=True)
    return summary


def self_test() -> None:
    assert norm("Nikola Jokić") == norm("Nikola Jokic")
    assert callable(v2.cached_complete)
    assert callable(batch.collect_one)
    print("run_corrected_off_batch_v3 self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=80)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        run(args.batch_size, args.workers)


if __name__ == "__main__":
    main()
