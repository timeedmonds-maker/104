from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import time
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import run_corrected_off_batch_v5 as v5

v4 = v5.v4
v3 = v4.v3
v2 = v4.v2
batch = v4.batch
core = v4.core
reuse = v2.reuse


def now() -> str:
    return v4.now()


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def group_key(window: dict[str, Any]) -> tuple[str, int, str, str]:
    start, end = core.query_dates(window)
    return str(window.get("season") or ""), int(window.get("team_id") or 0), start, end


def grouped_results(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("results")
    if not isinstance(raw, dict):
        return {}
    output: dict[str, list[dict[str, Any]]] = {}
    for metric, rows in raw.items():
        if isinstance(rows, list):
            clean = [row for row in rows if isinstance(row, dict)]
            if clean:
                output[str(metric)] = clean
    return output


@lru_cache(maxsize=1024)
def core_checkpoint(season: str, team_id: int) -> dict[str, Any]:
    path = reuse.CORE_CHECKPOINTS / season / f"{team_id}.json.gz"
    return reuse.load_gzip_json(path)


@lru_cache(maxsize=1024)
def expected_metric_names(season: str, team_id: int) -> tuple[str, ...]:
    checkpoint = core_checkpoint(season, team_id)
    results = checkpoint.get("team_on_off_results")
    if not isinstance(results, dict):
        return ()
    return tuple(sorted(str(metric) for metric in results.keys()))


def canonical_name(window: dict[str, Any]) -> str:
    season, team_id, _, _ = group_key(window)
    player_id = core.clean_id(window.get("player_id"))
    fallback = str(window.get("player_name") or window.get("player") or "").strip()
    checkpoint = core_checkpoint(season, team_id)
    return reuse.canonical_player_name(checkpoint, player_id, fallback)


def target_row(rows: list[dict[str, Any]], window: dict[str, Any]) -> dict[str, Any] | None:
    player_id = core.clean_id(window.get("player_id"))
    by_id = [
        row for row in rows
        if core.clean_id(row.get("PlayerId") or row.get("EntityId") or row.get("PlayerID")) == player_id
    ]
    if len(by_id) == 1:
        return by_id[0]
    names = {
        norm(window.get("player_name") or window.get("player")),
        norm(canonical_name(window)),
    }
    names.discard("")
    by_name = [row for row in rows if norm(row.get("Name")) in names]
    return by_name[0] if len(by_name) == 1 else None


def write_window_cache(window: dict[str, Any], result: dict[str, Any]) -> Path:
    path = batch.window_cache_path(window)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                existing = json.load(handle)
            if existing.get("complete") is True:
                return path
        except Exception:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    tmp.replace(path)
    return path


def extract_group_window(window: dict[str, Any], payload: dict[str, Any], request_meta: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    season, team_id, start, end = group_key(window)
    results = grouped_results(payload)
    expected = expected_metric_names(season, team_id)
    if not expected:
        return None, "core_metric_dictionary_missing"
    if set(results) != set(expected):
        return None, f"group_metric_set_mismatch_{len(results)}_vs_{len(expected)}"

    metrics: list[dict[str, Any]] = []
    minute_pairs: set[tuple[float, float]] = set()
    minute_source: dict[str, Any] | None = None
    for metric in expected:
        row = target_row(results.get(metric) or [], window)
        if row is None:
            return None, f"group_player_missing_{metric}"
        minutes_on = core.finite(row.get("MinutesOn"))
        minutes_off = core.finite(row.get("MinutesOff"))
        if minutes_on is not None and minutes_off is not None:
            minute_pairs.add((minutes_on, minutes_off))
            if minute_source is None:
                minute_source = row
        metrics.append({
            "metric": metric,
            "on": core.finite(row.get("On")),
            "off_corrected": core.finite(row.get("Off")),
            "on_minus_off_corrected": core.finite(row.get("On-Off")),
            "source_extra": {
                key: value for key, value in row.items()
                if key not in {"Name", "MinutesOn", "MinutesOff", "On", "Off", "On-Off"}
            },
        })

    if len(metrics) != len(expected):
        return None, "group_metric_count_incomplete"
    if len(minute_pairs) != 1:
        return None, f"group_minutes_inconsistent_{len(minute_pairs)}"
    minutes_on, minutes_off = next(iter(minute_pairs))
    player_id = core.clean_id(window.get("player_id"))
    player_name = canonical_name(window) or str(window.get("player_name") or window.get("player") or "").strip()
    result = {
        "complete": True,
        "season": season,
        "team_id": team_id,
        "team_abbr": window.get("team_abbr"),
        "player_id": player_id,
        "player": player_name,
        "tenure_start": window.get("tenure_start"),
        "tenure_end": window.get("tenure_end"),
        "query_start_date": start,
        "query_end_date": end,
        "transaction_day_policy": window.get("transaction_day_policy"),
        "team_games_in_window": window.get("team_games_in_window"),
        "tenure_source": window.get("source") or window.get("sources"),
        "tenure_confidence": window.get("confidence") or window.get("tenure_confidence"),
        "boundary_resolution": window.get("same_day_resolution"),
        "minutes_on": minutes_on,
        "minutes_off": minutes_off,
        "metric_count": len(metrics),
        "metrics": metrics,
        "minute_source_row": minute_source,
        "requests": {
            "group_team": request_meta,
            "stat_minutes": {
                "ok": True,
                "skipped": True,
                "reason": "MinutesOn/MinutesOff supplied directly by grouped team on-off rows",
            },
        },
        "collection_source": "interval_group_team_profile",
        "generated_utc": now(),
    }
    write_window_cache(window, result)
    return result, None


def group_request(params: dict[str, str], mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """One TeamId+date request for every player in an interval; no PlayerId and no STAT call."""
    v4.bump("request_calls")
    path = v4.net_cache_path(core.TEAM_URL, params)
    cached = v4.read_net_cache(path)
    if cached is not None and grouped_results(cached):
        v4.bump("cache_hits")
        return cached, {
            "ok": True, "attempt": 0, "status_code": 200, "cache_hit": True,
            "network_cache": str(path), "grouped": True, "errors": [],
        }

    lock = v4.get_cache_lock(path)
    with lock:
        cached = v4.read_net_cache(path)
        if cached is not None and grouped_results(cached):
            v4.bump("cache_hits")
            return cached, {
                "ok": True, "attempt": 0, "status_code": 200, "cache_hit": True,
                "network_cache": str(path), "grouped": True, "errors": [],
            }

        max_attempts = 1 if mode == "fresh" else 2
        errors: list[str] = []
        statuses: list[int | None] = []
        session = core.http_session()
        for attempt in range(1, max_attempts + 1):
            v4.rate_limit()
            started = time.monotonic()
            v4.bump("network_requests")
            try:
                response = session.get(core.TEAM_URL, params=params, timeout=(6, 35))
                status = int(response.status_code)
                statuses.append(status)
                if status == 503:
                    v4.bump("http_503")
                elif status == 429:
                    v4.bump("http_429")
                elif response.ok:
                    v4.bump("http_success")
                else:
                    v4.bump("http_other_error")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
                grouped = grouped_results(payload)
                if grouped:
                    v4.write_net_cache(path, payload)
                return payload, {
                    "ok": True,
                    "attempt": attempt,
                    "status_code": status,
                    "status_history": statuses,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "cache_hit": False,
                    "network_cache": str(path),
                    "grouped": bool(grouped),
                    "group_metric_count": len(grouped),
                    "errors": errors,
                }
            except Exception as exc:
                if not statuses:
                    v4.bump("exceptions")
                errors.append(f"attempt {attempt}: {exc!r}")
                if attempt < max_attempts:
                    time.sleep(1.0 + random.random() * 1.5)
        return {}, {
            "ok": False,
            "attempt": max_attempts,
            "status_code": statuses[-1] if statuses else None,
            "status_history": statuses,
            "cache_hit": False,
            "network_cache": str(path),
            "grouped": False,
            "errors": errors,
        }


def failure_record(window: dict[str, Any], previous: dict[str, Any], failure_class: str, group_attempts: int, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = {
        "attempts": int(previous.get("attempts") or 0) + 1,
        "group_attempts": group_attempts,
        "last_attempt_utc": now(),
        "season": window.get("season"),
        "team_id": window.get("team_id"),
        "team_abbr": window.get("team_abbr"),
        "player_id": window.get("player_id"),
        "player_name": window.get("player_name") or window.get("player"),
        "query_start_date": window.get("query_start_date"),
        "query_end_date": window.get("query_end_date"),
        "failure_class": failure_class,
    }
    if meta:
        if meta.get("status_code") is not None:
            detail["group_team_status"] = meta.get("status_code")
        detail["group_response_shape_valid"] = bool(meta.get("grouped"))
    return detail


def adaptive_settings(workers: int, request_interval: float, transient_rate: float, successes: int, selected_windows: int) -> tuple[int, float]:
    if transient_rate >= 0.45:
        return 1, min(0.80, max(0.35, request_interval) + 0.10)
    if transient_rate >= 0.20:
        return max(1, min(workers, 2)), min(0.50, max(0.22, request_interval) + 0.04)
    if transient_rate <= 0.05 and successes >= max(5, selected_windows // 4):
        return min(3, workers + 1), max(0.08, request_interval - 0.04)
    return workers, request_interval


def run(group_limit: int = 120, workers: int = 1, request_interval: float = 0.50) -> dict[str, Any]:
    if group_limit < 1:
        raise ValueError("group_limit must be >=1")
    if workers < 1 or workers > 3:
        raise ValueError("workers must be between 1 and 3")
    if request_interval < 0:
        raise ValueError("request_interval must be >=0")

    windows = batch.impact_windows()
    batch.prepare_cache_index(windows)
    core_keys = v4.load_or_build_core_index(windows)
    batch.cached_complete = v4.fast_cached_complete
    pending = [window for window in windows if not v4.fast_cached_complete(window)]
    pending_by_key = {v4.window_key(window): window for window in pending}
    state = v4.load_queue()
    state = {key: value for key, value in state.items() if key in pending_by_key}

    grouped: dict[tuple[str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for window in pending:
        grouped[group_key(window)].append(window)

    fresh_groups: list[tuple[tuple[str, int, str, str], list[dict[str, Any]]]] = []
    retry_groups: list[tuple[tuple[str, int, str, str], list[dict[str, Any]]]] = []
    for key, members in grouped.items():
        group_attempts = max(int(state.get(v4.window_key(window), {}).get("group_attempts") or 0) for window in members)
        if group_attempts == 0:
            fresh_groups.append((key, members))
        else:
            retry_groups.append((key, members))

    if fresh_groups:
        mode = "fresh"
        fresh_groups.sort(key=lambda item: (-len(item[1]), item[0]))
        selected = fresh_groups[:group_limit]
    else:
        mode = "retry"
        retry_groups.sort(key=lambda item: (
            max(int(state.get(v4.window_key(window), {}).get("group_attempts") or 0) for window in item[1]),
            -len(item[1]),
            item[0],
        ))
        selected = retry_groups[:max(20, min(group_limit // 2, 60))]

    v4._NETWORK_MODE = mode
    v4._REQUEST_INTERVAL = request_interval
    v4.reset_net_counts()
    started = time.monotonic()
    selected_windows = sum(len(members) for _, members in selected)
    successes = 0
    extraction_failures = 0
    failed_groups = 0
    valid_group_responses = 0
    invalid_group_responses = 0
    group_sizes = [len(members) for _, members in selected]

    # Intentionally start with one request stream. A grouped response can be much larger
    # than a focal-player response; concurrency is raised only after source stability is observed.
    for index, (key, members) in enumerate(selected, 1):
        season, team_id, start, end = key
        params = {
            "Season": season,
            "SeasonType": "Regular Season",
            "TeamId": str(team_id),
            "FromDate": start,
            "ToDate": end,
        }
        payload, meta = group_request(params, mode)
        result_map = grouped_results(payload) if meta.get("ok") else {}
        if meta.get("ok") and result_map:
            valid_group_responses += 1
            for window in members:
                result, extract_error = extract_group_window(window, payload, meta)
                window_id = v4.window_key(window)
                previous = state.get(window_id, {})
                prior_group_attempts = int(previous.get("group_attempts") or 0)
                if result is not None and result.get("complete") is True:
                    state.pop(window_id, None)
                    successes += 1
                else:
                    extraction_failures += 1
                    state[window_id] = failure_record(
                        window, previous, extract_error or "group_extract_incomplete", prior_group_attempts + 1, meta
                    )
        else:
            failed_groups += 1
            if meta.get("ok") and not result_map:
                invalid_group_responses += 1
            status = meta.get("status_code")
            failure_class = f"group_team_http_{status or 'error'}" if not meta.get("ok") else "group_response_shape_invalid"
            for window in members:
                window_id = v4.window_key(window)
                previous = state.get(window_id, {})
                prior_group_attempts = int(previous.get("group_attempts") or 0)
                state[window_id] = failure_record(window, previous, failure_class, prior_group_attempts + 1, meta)

        if index % 20 == 0 or index == len(selected):
            stats = v4.net_snapshot()
            print(
                f"v6 grouped {mode} groups={index}/{len(selected)} windows={selected_windows} "
                f"window_successes={successes} net={stats.get('network_requests', 0)} "
                f"cache_hits={stats.get('cache_hits', 0)} 503={stats.get('http_503', 0)}",
                flush=True,
            )

    v4.save_queue(state)
    summary = batch.assemble(windows)
    elapsed = round(time.monotonic() - started, 3)
    stats = v4.net_snapshot()
    network_requests = int(stats.get("network_requests") or 0)
    transient = int(stats.get("http_503") or 0) + int(stats.get("http_429") or 0)
    transient_rate = transient / network_requests if network_requests else 0.0
    next_workers, next_interval = adaptive_settings(workers, request_interval, transient_rate, successes, selected_windows)
    api_calls_per_success = (network_requests / successes) if successes else None
    windows_per_network_request = (selected_windows / network_requests) if network_requests else None
    group_support: bool | None
    if valid_group_responses:
        group_support = True
    elif invalid_group_responses:
        group_support = False
    else:
        group_support = None

    summary.update({
        "stage2_v6": True,
        "selection_mode": mode,
        "grouped_team_interval_mode": True,
        "group_response_supported": group_support,
        "selected_groups": len(selected),
        "selected_windows": selected_windows,
        "batch_successes": successes,
        "batch_errors": selected_windows - successes,
        "batch_elapsed_seconds": elapsed,
        "batch_workers": 1,
        "request_interval_seconds": request_interval,
        "recommended_workers": next_workers,
        "recommended_request_interval_seconds": round(next_interval, 3),
        "fresh_groups_before": len(fresh_groups),
        "retry_groups_before": len(retry_groups),
        "deferred_queue_windows": len(state),
        "valid_group_responses": valid_group_responses,
        "invalid_group_responses": invalid_group_responses,
        "failed_groups": failed_groups,
        "group_extraction_failures": extraction_failures,
        "group_size_max": max(group_sizes) if group_sizes else 0,
        "group_size_mean": round(sum(group_sizes) / len(group_sizes), 3) if group_sizes else 0.0,
        "network_counters": stats,
        "transient_http_failure_rate": round(transient_rate, 6),
        "api_calls_per_completed_window_this_round": round(api_calls_per_success, 4) if api_calls_per_success is not None else None,
        "selected_windows_per_network_request": round(windows_per_network_request, 4) if windows_per_network_request is not None else None,
        "core_reuse_index_windows": len(core_keys),
        "network_cache_root": str(v4.NET_ROOT),
        "network_cache_committed_to_git": False,
        "batch_error_examples": list(state.values())[:10],
        "network_policy": (
            "Stage2 v6 requests /get-on-off/nba/team once per unique season/team/date interval WITHOUT PlayerId. "
            "A valid grouped response supplies every metric plus MinutesOn/MinutesOff for every player in that interval, "
            "eliminating both per-player TEAM calls and the separate STAT minutes call. Exact Stage1 tenure boundaries are unchanged. "
            "Grouped response shape and full core metric set are validated before any window is marked complete."
        ),
    })
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "complete_windows": summary.get("complete_windows"),
        "remaining_windows": summary.get("remaining_windows"),
        "selected_groups": len(selected),
        "selected_windows": selected_windows,
        "window_successes": successes,
        "group_response_supported": group_support,
        "network_counters": stats,
        "transient_http_failure_rate": round(transient_rate, 4),
        "api_calls_per_completed_window": round(api_calls_per_success, 4) if api_calls_per_success is not None else None,
        "next_workers": next_workers,
        "next_interval": round(next_interval, 3),
    }, indent=2), flush=True)
    return summary


def self_test() -> None:
    payload = {
        "results": {
            "OffRebounds": [{"Name": "Nikola Jokić", "MinutesOn": 10, "MinutesOff": 20, "On": 4, "Off": 5, "On-Off": -1}],
            "DefRebounds": [{"Name": "Nikola Jokić", "MinutesOn": 10, "MinutesOff": 20, "On": 8, "Off": 9, "On-Off": -1}],
        }
    }
    assert set(grouped_results(payload)) == {"OffRebounds", "DefRebounds"}
    assert norm("Nikola Jokić") == norm("Nikola Jokic")
    sample = {"season": "2023-24", "team_id": 1, "player_id": "2", "query_start_date": "2024-01-01", "query_end_date": "2024-01-02"}
    assert group_key(sample) == ("2023-24", 1, "2024-01-01", "2024-01-02")
    print("run_corrected_off_batch_v6 self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-limit", type=int, default=120)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--request-interval", type=float, default=0.50)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        run(args.group_limit, args.workers, args.request_interval)


if __name__ == "__main__":
    main()
