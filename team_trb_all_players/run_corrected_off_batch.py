from __future__ import annotations

import argparse
import gzip
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import build_corrected_tenure_off as core

CACHE_INDEX_MARKER = core.CACHE / ".complete_cache_index_v1"


def window_cache_path(window: dict[str, Any]) -> Path:
    start, end = core.query_dates(window)
    return core.cache_path(window, start, end)


def prepare_cache_index(windows: list[dict[str, Any]]) -> None:
    """One-time migration: after this marker exists, cache-file existence means complete.

    Older collector versions wrote incomplete request results to the same cache directory,
    forcing every batch to gunzip and parse every completed file repeatedly. Clean those
    once, then use cheap path existence checks for all subsequent resumable batches.
    """
    core.CACHE.mkdir(parents=True, exist_ok=True)
    if CACHE_INDEX_MARKER.exists():
        return
    checked = removed = complete = 0
    for window in windows:
        path = window_cache_path(window)
        if not path.exists():
            continue
        checked += 1
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("complete") is True:
                complete += 1
                continue
        except Exception:
            pass
        path.unlink(missing_ok=True)
        removed += 1
    CACHE_INDEX_MARKER.write_text(
        json.dumps({"checked": checked, "complete": complete, "removed_incomplete": removed}, indent=2),
        encoding="utf-8",
    )
    print(
        f"corrected OFF cache index prepared: checked={checked} complete={complete} removed_incomplete={removed}",
        flush=True,
    )


def cached_complete(window: dict[str, Any]) -> bool:
    return window_cache_path(window).exists()


def read_cached(window: dict[str, Any]) -> dict[str, Any]:
    path = window_cache_path(window)
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
    complete_windows = [window for window in windows if cached_complete(window)]
    missing = len(windows) - len(complete_windows)
    metric_rows: int | None = None
    core.OUT.mkdir(parents=True, exist_ok=True)

    # Avoid repeatedly gunzipping every completed tenure after each partial batch.
    # Materialize and count metric rows once, only when the cache is fully complete.
    if missing == 0:
        metric_rows = 0
        with gzip.open(core.LONG, "wt", encoding="utf-8") as handle:
            for window in complete_windows:
                result = read_cached(window)
                if result.get("complete") is not True:
                    raise RuntimeError(f"cache index invariant violated for {window_cache_path(window)}")
                base = {k: v for k, v in result.items() if k not in {"metrics", "minute_source_row", "requests"}}
                for metric in result.get("metrics") or []:
                    handle.write(json.dumps({**base, **metric}, ensure_ascii=False) + "\n")
                    metric_rows += 1

    summary = {
        "generated_utc": core.now(),
        "stage1_exact_ready": True,
        "impact_windows_total": len(windows),
        "complete_windows": len(complete_windows),
        "remaining_windows": missing,
        "failed_windows": 0,
        "metric_rows": metric_rows,
        "all_complete": missing == 0,
        "output": str(core.LONG) if missing == 0 else None,
        "cache": str(core.CACHE),
        "policy": "resumable tenure-scoped PBP Stats on/off collection; original core is never rerun; teammate pairs excluded",
    }
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def discard_incomplete_cache(window: dict[str, Any]) -> None:
    try:
        window_cache_path(window).unlink(missing_ok=True)
    except Exception:
        pass


def collect_one(window: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        result = core.collect_window(window)
        if result.get("complete") is True:
            return result, None
        discard_incomplete_cache(window)
        return result, {"window": window, "result": result}
    except Exception as exc:
        discard_incomplete_cache(window)
        return {}, {"window": window, "error": repr(exc)}


def run(batch_size: int, workers: int = 1) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    windows = impact_windows()
    prepare_cache_index(windows)
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
    assert callable(prepare_cache_index)
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
