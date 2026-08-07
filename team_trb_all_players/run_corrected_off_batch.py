from __future__ import annotations

import argparse
import gzip
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import build_corrected_tenure_off as core


def cached_complete(window: dict[str, Any]) -> bool:
    try:
        start, end = core.query_dates(window)
        path = core.cache_path(window, start, end)
        if not path.exists():
            return False
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("complete") is True
    except Exception:
        return False


def read_cached(window: dict[str, Any]) -> dict[str, Any]:
    start, end = core.query_dates(window)
    path = core.cache_path(window, start, end)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def impact_windows() -> list[dict[str, Any]]:
    review = core.load_json(core.REVIEW)
    if review.get("stage1_exact_ready") is not True or int(review.get("review_queue_windows") or 0) != 0:
        raise RuntimeError(f"Stage 1 not exact-ready: {review}")
    windows = core.load_rows(core.WINDOWS)
    unresolved = [w for w in windows if w.get("schedule_boundary_status") != "resolved"]
    if unresolved:
        raise RuntimeError(f"exact-ready gate conflicts with {len(unresolved)} unresolved windows")
    return [w for w in windows if core.clean_id(w.get("player_id")) and not bool(w.get("zero_minute_only"))]


def assemble(windows: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for window in windows:
        try:
            result = read_cached(window)
        except Exception:
            continue
        if result.get("complete") is True:
            results.append(result)
    missing = len(windows) - len(results)
    complete = results
    core.OUT.mkdir(parents=True, exist_ok=True)
    if missing == 0:
        with gzip.open(core.LONG, "wt", encoding="utf-8") as handle:
            for result in complete:
                base = {k: v for k, v in result.items() if k not in {"metrics", "minute_source_row", "requests"}}
                for metric in result.get("metrics") or []:
                    handle.write(json.dumps({**base, **metric}, ensure_ascii=False) + "\n")
    summary = {
        "generated_utc": core.now(),
        "stage1_exact_ready": True,
        "impact_windows_total": len(windows),
        "complete_windows": len(complete),
        "remaining_windows": missing,
        "failed_windows": 0,
        "metric_rows": sum(len(r.get("metrics") or []) for r in complete),
        "all_complete": missing == 0,
        "output": str(core.LONG) if missing == 0 else None,
        "cache": str(core.CACHE),
        "policy": "resumable tenure-scoped PBP Stats on/off collection; original core is never rerun; teammate pairs excluded",
    }
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def collect_one(window: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        result = core.collect_window(window)
        if result.get("complete") is True:
            return result, None
        return result, {"window": window, "result": result}
    except Exception as exc:
        return {}, {"window": window, "error": repr(exc)}


def run(batch_size: int, workers: int = 1) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    windows = impact_windows()
    pending = [w for w in windows if not cached_complete(w)]
    selected = pending[:batch_size]
    errors: list[dict[str, Any]] = []
    completed = 0

    if workers == 1:
        for window in selected:
            _, error = collect_one(window)
            if error:
                errors.append(error)
            completed += 1
            if completed % 25 == 0 or completed == len(selected):
                print(f"batch {completed}/{len(selected)} workers=1 errors={len(errors)}", flush=True)
    else:
        # Each tenure window writes to a unique cache path. Keep concurrency deliberately
        # bounded so PBP Stats is accelerated without turning this into an API flood.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="treb-off") as pool:
            futures = {pool.submit(collect_one, window): window for window in selected}
            for future in as_completed(futures):
                _, error = future.result()
                if error:
                    errors.append(error)
                completed += 1
                if completed % 25 == 0 or completed == len(selected):
                    print(f"batch {completed}/{len(selected)} workers={workers} errors={len(errors)}", flush=True)

    summary = assemble(windows)
    summary["batch_requested"] = len(selected)
    summary["batch_errors"] = len(errors)
    summary["batch_workers"] = workers
    summary["batch_error_examples"] = errors[:20]
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if errors:
        raise RuntimeError(f"batch had {len(errors)} failed tenure windows")
    return summary


def self_test() -> None:
    assert callable(cached_complete)
    assert callable(assemble)
    assert callable(collect_one)
    print("run_corrected_off_batch self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(run(args.batch_size, args.workers), indent=2))


if __name__ == "__main__":
    main()
