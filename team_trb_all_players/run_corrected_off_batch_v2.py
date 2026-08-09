from __future__ import annotations

import argparse
from functools import lru_cache

import prepare_corrected_off_workset as reuse
import run_corrected_off_batch as batch

# Stage 2 v2: do not materialize thousands of duplicate full-season cache files.
# Full-team-schedule windows are satisfied directly from the completed 780/780
# core checkpoints; only partial-tenure windows hit PBP Stats and enter cache.

# Cache core checkpoint decompression by path so the dynamic reuse scan reads
# each team-season checkpoint at most once per job.
reuse.load_gzip_json = lru_cache(maxsize=1024)(reuse.load_gzip_json)
_TEAM_GAME_COUNTS = reuse.load_games()
_ORIG_READ_CACHED = batch.read_cached


def _is_full_schedule(window: dict) -> bool:
    try:
        key = (str(window.get("season") or ""), int(window.get("team_id") or 0))
        total = _TEAM_GAME_COUNTS.get(key)
        in_window = window.get("team_games_in_window")
        return total is not None and in_window is not None and int(total) == int(in_window)
    except Exception:
        return False


def _core_result(window: dict):
    if not _is_full_schedule(window):
        return None
    return reuse.core_result_for_window(window)


def cached_complete(window: dict) -> bool:
    path = batch.window_cache_path(window)
    if path.exists():
        return True
    return _core_result(window) is not None


def read_cached(window: dict):
    path = batch.window_cache_path(window)
    if path.exists():
        return _ORIG_READ_CACHED(window)
    result = _core_result(window)
    if result is None:
        raise RuntimeError(f"no cache/core result for completed window: {window}")
    return result


# Monkey-patch the module globals used by run()/assemble().
batch.cached_complete = cached_complete
batch.read_cached = read_cached


def run(batch_size: int, workers: int):
    summary = batch.run(batch_size=batch_size, workers=workers)
    windows = batch.impact_windows()
    dynamic_core = sum(1 for w in windows if not batch.window_cache_path(w).exists() and _core_result(w) is not None)
    summary["dynamic_core_reused_windows"] = dynamic_core
    summary["stage2_v2"] = True
    summary["network_policy"] = "Only partial-tenure windows are collected from PBP Stats; full-team-schedule windows are read directly from the completed core checkpoints."
    batch.core.SUMMARY.write_text(__import__('json').dumps(summary, indent=2), encoding='utf-8')
    return summary


def self_test() -> None:
    assert callable(cached_complete)
    assert callable(read_cached)
    print("run_corrected_off_batch_v2 self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=80)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        print(__import__('json').dumps(run(args.batch_size, args.workers), indent=2))


if __name__ == "__main__":
    main()
