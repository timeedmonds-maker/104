from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any

import run_corrected_off_batch_v5 as v5

v4 = v5.v4
core = v5.core

TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def _retry_after_seconds(response: Any) -> float | None:
    try:
        raw = response.headers.get("Retry-After")
    except Exception:
        return None
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except Exception:
        return None
    if value < 0:
        return None
    return min(value, 30.0)


def public_resilient_request_json(
    url: str,
    params: dict[str, str],
    attempts: int = 4,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the proven public-runner retry pattern without changing Stage2 cache semantics.

    Successful payloads use the existing v4 endpoint cache. Network requests are globally
    paced by v4.rate_limit(). Transient 429/5xx responses and transport exceptions receive
    up to four attempts with exponential backoff plus jitter, matching the earlier successful
    public Actions collector rather than immediately quarantining first-attempt 503s.
    """
    v4.bump("request_calls")
    path = v4.net_cache_path(url, params)
    cached = v4.read_net_cache(path)
    if cached is not None:
        v4.bump("cache_hits")
        return cached, {
            "ok": True,
            "attempt": 0,
            "status_code": 200,
            "cache_hit": True,
            "network_cache": str(path),
            "errors": [],
            "public_resilient": True,
        }

    lock = v4.get_cache_lock(path)
    with lock:
        cached = v4.read_net_cache(path)
        if cached is not None:
            v4.bump("cache_hits")
            return cached, {
                "ok": True,
                "attempt": 0,
                "status_code": 200,
                "cache_hit": True,
                "network_cache": str(path),
                "errors": [],
                "public_resilient": True,
            }

        max_attempts = max(1, min(int(attempts or 4), 4))
        errors: list[str] = []
        statuses: list[int | None] = []
        session = core.http_session()

        for attempt in range(1, max_attempts + 1):
            v4.rate_limit()
            started = time.monotonic()
            v4.bump("network_requests")
            response = None
            transient = False
            try:
                response = session.get(url, params=params, timeout=(10, 35))
                status = int(response.status_code)
                statuses.append(status)
                transient = status in TRANSIENT_STATUSES

                if status == 503:
                    v4.bump("http_503")
                elif status == 429:
                    v4.bump("http_429")
                elif response.ok:
                    v4.bump("http_success")
                else:
                    v4.bump("http_other_error")

                if transient:
                    raise RuntimeError(f"HTTP {status}: {response.text[:160]!r}")

                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
                if core.rows(payload):
                    v4.write_net_cache(path, payload)
                if attempt > 1:
                    v4.bump("retry_recoveries")
                return payload, {
                    "ok": True,
                    "attempt": attempt,
                    "status_code": status,
                    "status_history": statuses,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "cache_hit": False,
                    "network_cache": str(path),
                    "errors": errors,
                    "public_resilient": True,
                    "recovered_after_retry": attempt > 1,
                }
            except Exception as exc:
                if response is None:
                    statuses.append(None)
                    v4.bump("exceptions")
                    transient = True
                errors.append(f"attempt {attempt}: {exc!r}")
                if attempt >= max_attempts or not transient:
                    break
                v4.bump("transient_retries")
                retry_after = _retry_after_seconds(response) if response is not None else None
                delay = retry_after if retry_after is not None else min(2 ** (attempt - 1), 8) + random.random()
                time.sleep(max(0.0, delay))

        return {}, {
            "ok": False,
            "attempt": len(statuses),
            "status_code": statuses[-1] if statuses else None,
            "status_history": statuses,
            "cache_hit": False,
            "network_cache": str(path),
            "errors": errors,
            "public_resilient": True,
        }


def run(batch_size: int, workers: int, request_interval: float) -> dict[str, Any]:
    # v5 deliberately points core.request_json at v4.cached_request_json. Replace that
    # function object before v5.run so all existing Stage2 logic uses this request layer.
    v4.cached_request_json = public_resilient_request_json
    summary = v5.run(batch_size, workers, request_interval)
    summary["public_resilient_request_layer"] = True
    summary["public_resilient_max_attempts"] = 4
    summary["public_resilient_transient_statuses"] = sorted(TRANSIENT_STATUSES)
    summary["public_resilient_request_interval_seconds"] = request_interval
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    assert TRANSIENT_STATUSES == {429, 500, 502, 503, 504}
    assert _retry_after_seconds(type("R", (), {"headers": {"Retry-After": "2"}})()) == 2.0
    assert _retry_after_seconds(type("R", (), {"headers": {}})()) is None
    print("run_corrected_off_batch_public_resilient self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=70)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--request-interval", type=float, default=1.0)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(run(args.batch_size, args.workers, args.request_interval), indent=2))


if __name__ == "__main__":
    main()
