from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_corrected_off_batch_v3 as v3

v2 = v3.v2
batch = v3.batch
core = batch.core

QUEUE = core.OUT / "deferred_failure_queue.json"
CORE_INDEX = core.OUT / "core_reuse_complete_keys_v1.json"
NET_ROOT = Path(os.environ.get("TREB_STAGE2_NET_CACHE", "/workspaces/.treb_stage2_netcache_v4"))
TEAM_NET = NET_ROOT / "team"
STAT_NET = NET_ROOT / "stat"

_NETWORK_MODE = "fresh"
_REQUEST_INTERVAL = 0.12
_rate_lock = threading.Lock()
_next_request_at = 0.0
_cache_lock_guard = threading.Lock()
_cache_locks: dict[str, threading.Lock] = {}
_net_counter_lock = threading.Lock()
_net_counts: dict[str, int] = {}
_core_complete_keys: set[str] | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_net_counts() -> None:
    global _net_counts
    with _net_counter_lock:
        _net_counts = {
            "request_calls": 0,
            "network_requests": 0,
            "cache_hits": 0,
            "http_success": 0,
            "http_503": 0,
            "http_429": 0,
            "http_other_error": 0,
            "exceptions": 0,
        }


def bump(name: str, n: int = 1) -> None:
    with _net_counter_lock:
        _net_counts[name] = int(_net_counts.get(name, 0)) + n


def net_snapshot() -> dict[str, int]:
    with _net_counter_lock:
        return dict(_net_counts)


def window_key(window: dict[str, Any]) -> str:
    return batch.window_cache_path(window).name


def stat_group_key(window: dict[str, Any]) -> tuple[str, int, str, str]:
    start, end = core.query_dates(window)
    return (
        str(window.get("season") or ""),
        int(window.get("team_id") or 0),
        start,
        end,
    )


def load_or_build_core_index(windows: list[dict[str, Any]]) -> set[str]:
    global _core_complete_keys
    if _core_complete_keys is not None:
        return _core_complete_keys

    if CORE_INDEX.exists():
        try:
            data = json.loads(CORE_INDEX.read_text(encoding="utf-8"))
            keys = data.get("keys") if isinstance(data, dict) else None
            if isinstance(keys, list) and int(data.get("impact_windows_total") or -1) == len(windows):
                _core_complete_keys = set(str(k) for k in keys)
                return _core_complete_keys
        except Exception:
            pass

    keys: set[str] = set()
    checked_full = 0
    for i, window in enumerate(windows, 1):
        if not v2._is_full_schedule(window):
            continue
        checked_full += 1
        if v2._core_result(window) is not None:
            keys.add(window_key(window))
        if checked_full % 1000 == 0:
            print(f"v4 core-reuse index {checked_full} full-schedule windows checked", flush=True)

    CORE_INDEX.parent.mkdir(parents=True, exist_ok=True)
    CORE_INDEX.write_text(json.dumps({
        "generated_utc": now(),
        "impact_windows_total": len(windows),
        "core_reusable_windows": len(keys),
        "policy": "Persistent O(1) membership index for completed 780/780 core reuse; avoids rebuilding full core results on every Stage2 batch.",
        "keys": sorted(keys),
    }, indent=2), encoding="utf-8")
    _core_complete_keys = keys
    print(f"v4 core-reuse index ready: {len(keys)} windows", flush=True)
    return keys


def fast_cached_complete(window: dict[str, Any]) -> bool:
    path = batch.window_cache_path(window)
    if path.exists():
        return True
    return window_key(window) in (_core_complete_keys or set())


def cache_key(url: str, params: dict[str, str]) -> str:
    canonical = json.dumps([url, sorted((str(k), str(v)) for k, v in params.items())], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def net_cache_path(url: str, params: dict[str, str]) -> Path:
    root = STAT_NET if url == core.STAT_URL else TEAM_NET
    digest = cache_key(url, params)
    return root / digest[:2] / f"{digest}.json.gz"


def get_cache_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _cache_lock_guard:
        lock = _cache_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _cache_locks[key] = lock
        return lock


def read_net_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        payload = data.get("payload") if isinstance(data, dict) else None
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def write_net_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump({"generated_utc": now(), "payload": payload}, handle, ensure_ascii=False)
    tmp.replace(path)


def rate_limit() -> None:
    global _next_request_at
    with _rate_lock:
        current = time.monotonic()
        wait = _next_request_at - current
        if wait > 0:
            time.sleep(wait)
            current = time.monotonic()
        _next_request_at = current + max(0.0, float(_REQUEST_INTERVAL))


def cached_request_json(url: str, params: dict[str, str], attempts: int = 4) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cache successful endpoint payloads and avoid wasteful 4x retries on fresh windows.

    Fresh pass: one network attempt, then defer on transient failure.
    Retry pass: at most two attempts with short jittered backoff.
    Successful stat payloads are naturally shared across every player with the
    same season/team/date interval because PlayerId is not part of that request.
    """
    bump("request_calls")
    path = net_cache_path(url, params)
    cached = read_net_cache(path)
    if cached is not None:
        bump("cache_hits")
        return cached, {
            "ok": True,
            "attempt": 0,
            "status_code": 200,
            "cache_hit": True,
            "network_cache": str(path),
            "errors": [],
        }

    lock = get_cache_lock(path)
    with lock:
        cached = read_net_cache(path)
        if cached is not None:
            bump("cache_hits")
            return cached, {
                "ok": True,
                "attempt": 0,
                "status_code": 200,
                "cache_hit": True,
                "network_cache": str(path),
                "errors": [],
            }

        max_attempts = 1 if _NETWORK_MODE == "fresh" else 2
        errors: list[str] = []
        statuses: list[int | None] = []
        session = core.http_session()
        for attempt in range(1, max_attempts + 1):
            rate_limit()
            started = time.monotonic()
            bump("network_requests")
            try:
                response = session.get(url, params=params, timeout=(5, 25))
                statuses.append(int(response.status_code))
                if response.status_code == 503:
                    bump("http_503")
                elif response.status_code == 429:
                    bump("http_429")
                elif response.ok:
                    bump("http_success")
                else:
                    bump("http_other_error")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
                # Cache only useful, non-empty successful payloads. Empty responses can be retried later.
                if core.rows(payload):
                    write_net_cache(path, payload)
                return payload, {
                    "ok": True,
                    "attempt": attempt,
                    "status_code": response.status_code,
                    "status_history": statuses,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "cache_hit": False,
                    "network_cache": str(path),
                    "errors": errors,
                }
            except Exception as exc:
                if not statuses or statuses[-1] is None:
                    bump("exceptions")
                errors.append(f"attempt {attempt}: {exc!r}")
                if attempt < max_attempts:
                    # Only retry in deferred mode; keep waits short and jittered.
                    time.sleep(1.0 + random.random() * 1.5)

        return {}, {
            "ok": False,
            "attempt": max_attempts,
            "status_code": statuses[-1] if statuses else None,
            "status_history": statuses,
            "cache_hit": False,
            "network_cache": str(path),
            "errors": errors,
        }


def load_queue() -> dict[str, dict[str, Any]]:
    return v3.load_queue()


def save_queue(state: dict[str, dict[str, Any]]) -> None:
    v3.save_queue(state)


def run(batch_size: int, workers: int = 2, request_interval: float = 0.12) -> dict[str, Any]:
    global _NETWORK_MODE, _REQUEST_INTERVAL
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if workers < 1 or workers > 6:
        raise ValueError("workers must be between 1 and 6")
    if request_interval < 0:
        raise ValueError("request_interval must be >= 0")

    windows = batch.impact_windows()
    batch.prepare_cache_index(windows)
    core_keys = load_or_build_core_index(windows)
    batch.cached_complete = fast_cached_complete
    # read_cached remains v2's cache-or-core reader for the final one-time assembly.

    pending = [w for w in windows if not fast_cached_complete(w)]
    pending_by_key = {window_key(w): w for w in pending}
    state = load_queue()
    state = {k: v for k, v in state.items() if k in pending_by_key}

    fresh = [w for w in pending if window_key(w) not in state]
    deferred = [w for w in pending if window_key(w) in state]

    if fresh:
        mode = "fresh"
        # Keep identical team/date intervals together so the player-independent
        # stat endpoint is fetched once and reused by all matching windows.
        fresh.sort(key=lambda w: (stat_group_key(w), window_key(w)))
        selected = fresh[:batch_size]
        effective_workers = workers
    else:
        mode = "retry"
        deferred.sort(key=lambda w: (
            int(state.get(window_key(w), {}).get("attempts") or 0),
            str(state.get(window_key(w), {}).get("last_attempt_utc") or ""),
            stat_group_key(w),
            window_key(w),
        ))
        selected = deferred[:max(20, min(batch_size // 2, 80))]
        effective_workers = min(workers, 2)

    _NETWORK_MODE = mode
    _REQUEST_INTERVAL = request_interval
    reset_net_counts()
    errors: list[dict[str, Any]] = []
    successes = 0
    completed = 0
    started_batch = time.monotonic()

    if selected:
        with ThreadPoolExecutor(max_workers=effective_workers, thread_name_prefix="treb-off-v4") as pool:
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
                    attempts_count = int(previous.get("attempts") or 0) + 1
                    state[key] = v3.compact_error(window, error, attempts_count)
                    errors.append(error or {"window": window, "error": "incomplete result"})
                else:
                    state.pop(key, None)
                    successes += 1
                completed += 1
                if completed % 25 == 0 or completed == len(selected):
                    stats = net_snapshot()
                    print(
                        f"v4 {mode} {completed}/{len(selected)} workers={effective_workers} "
                        f"successes={successes} deferred_now={len(state)} net={stats.get('network_requests', 0)} "
                        f"cache_hits={stats.get('cache_hits', 0)} 503={stats.get('http_503', 0)}",
                        flush=True,
                    )

    save_queue(state)
    summary = batch.assemble(windows)
    elapsed = round(time.monotonic() - started_batch, 3)
    stats = net_snapshot()
    network_requests = int(stats.get("network_requests") or 0)
    transient = int(stats.get("http_503") or 0) + int(stats.get("http_429") or 0)
    transient_rate = transient / network_requests if network_requests else 0.0

    # AIMD-style concurrency: source stability, not CPU count, determines useful concurrency.
    if transient_rate >= 0.35:
        recommended_workers = max(1, effective_workers - 1)
        recommended_interval = min(0.50, max(request_interval, 0.22) + 0.08)
    elif transient_rate >= 0.15:
        recommended_workers = max(1, effective_workers)
        recommended_interval = min(0.35, max(request_interval, 0.16))
    elif transient_rate <= 0.05 and successes >= max(5, len(selected) // 3):
        recommended_workers = min(4, effective_workers + 1)
        recommended_interval = max(0.06, request_interval - 0.02)
    else:
        recommended_workers = effective_workers
        recommended_interval = request_interval

    summary.update({
        "stage2_v4": True,
        "selection_mode": mode,
        "batch_requested": len(selected),
        "batch_successes": successes,
        "batch_errors": len(errors),
        "batch_workers": effective_workers,
        "batch_elapsed_seconds": elapsed,
        "fresh_pending_before": len(fresh),
        "deferred_pending_before": len(deferred),
        "deferred_queue_windows": len(state),
        "fresh_first_policy": True,
        "internal_attempts_fresh": 1,
        "internal_attempts_retry": 2,
        "request_interval_seconds": request_interval,
        "recommended_workers": recommended_workers,
        "recommended_request_interval_seconds": round(recommended_interval, 3),
        "network_counters": stats,
        "transient_http_failure_rate": round(transient_rate, 6),
        "core_reuse_index_windows": len(core_keys),
        "network_cache_root": str(NET_ROOT),
        "network_cache_committed_to_git": False,
        "batch_error_examples": errors[:10],
        "network_policy": (
            "Full-team-schedule windows use a persistent core-reuse index. Partial-tenure fresh failures get one HTTP attempt, "
            "then are quarantined. Successful endpoint payloads are cached locally; player-independent stat payloads are shared "
            "across matching team/date intervals. Deferred retries use at most two attempts and adaptive lower concurrency."
        ),
    })
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "complete_windows": summary.get("complete_windows"),
        "remaining_windows": summary.get("remaining_windows"),
        "selection_mode": mode,
        "batch_successes": successes,
        "batch_errors": len(errors),
        "batch_elapsed_seconds": elapsed,
        "network_counters": stats,
        "transient_http_failure_rate": round(transient_rate, 4),
        "recommended_workers": recommended_workers,
        "recommended_request_interval_seconds": round(recommended_interval, 3),
        "fresh_pending_before": len(fresh),
        "deferred_queue_windows": len(state),
    }, indent=2), flush=True)
    return summary


def self_test() -> None:
    sample = {
        "season": "2000-01", "team_id": 1610612737, "player_id": "1",
        "query_start_date": "2000-10-31", "query_end_date": "2000-11-01",
    }
    assert window_key(sample).endswith(".json.gz")
    assert cache_key("u", {"b": "2", "a": "1"}) == cache_key("u", {"a": "1", "b": "2"})
    assert callable(v3.robust_stat_minutes)
    assert callable(cached_request_json)
    print("run_corrected_off_batch_v4 self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=160)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--request-interval", type=float, default=0.12)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        # Monkey patches are process-local and intentionally applied only to v4.
        core.request_json = cached_request_json
        run(args.batch_size, args.workers, args.request_interval)


if __name__ == "__main__":
    main()
