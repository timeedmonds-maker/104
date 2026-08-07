from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
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
    results = [read_cached(w) for w in windows if cached_complete(w)]
    missing = len(windows) - len(results)
    complete = [r for r in results if r.get("complete") is True]
    core.OUT.mkdir(parents=True, exist_ok=True)
    if missing == 0 and len(complete) == len(windows):
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
        "failed_windows": sum(not r.get("complete") for r in results),
        "metric_rows": sum(len(r.get("metrics") or []) for r in complete),
        "all_complete": missing == 0 and len(complete) == len(windows),
        "output": str(core.LONG) if missing == 0 else None,
        "cache": str(core.CACHE),
        "policy": "resumable tenure-scoped PBP Stats on/off collection; original core is never rerun; teammate pairs excluded",
    }
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run(batch_size: int) -> dict[str, Any]:
    windows = impact_windows()
    pending = [w for w in windows if not cached_complete(w)]
    selected = pending[:batch_size]
    errors: list[dict[str, Any]] = []
    for i, window in enumerate(selected, 1):
        try:
            result = core.collect_window(window)
            if not result.get("complete"):
                errors.append({"window": window, "result": result})
        except Exception as exc:
            errors.append({"window": window, "error": repr(exc)})
        if i % 25 == 0 or i == len(selected):
            print(f"batch {i}/{len(selected)} errors={len(errors)}", flush=True)
    summary = assemble(windows)
    summary["batch_requested"] = len(selected)
    summary["batch_errors"] = len(errors)
    summary["batch_error_examples"] = errors[:20]
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if errors:
        raise RuntimeError(f"batch had {len(errors)} failed tenure windows")
    return summary


def self_test() -> None:
    assert callable(cached_complete)
    assert callable(assemble)
    print("run_corrected_off_batch self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(run(args.batch_size), indent=2))


if __name__ == "__main__":
    main()
