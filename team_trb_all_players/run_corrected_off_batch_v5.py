from __future__ import annotations

import argparse
import json
from typing import Any

import run_corrected_off_batch_v4 as v4

v3 = v4.v3
core = v4.core


def compact_failure(window: dict[str, Any], error: dict[str, Any] | None, attempts: int) -> dict[str, Any]:
    """Keep the durable retry queue small: no 89-metric payloads or full request URLs."""
    detail: dict[str, Any] = {
        "attempts": attempts,
        "last_attempt_utc": v4.now(),
        "season": window.get("season"),
        "team_id": window.get("team_id"),
        "team_abbr": window.get("team_abbr"),
        "player_id": window.get("player_id"),
        "player_name": window.get("player_name") or window.get("player"),
        "query_start_date": window.get("query_start_date"),
        "query_end_date": window.get("query_end_date"),
    }
    if not error:
        detail["failure_class"] = "incomplete_unknown"
        return detail
    if error.get("error"):
        detail["failure_class"] = "collector_exception"
        detail["last_error"] = str(error.get("error"))[:300]
        return detail

    result = error.get("result") if isinstance(error.get("result"), dict) else {}
    detail["last_complete"] = bool(result.get("complete"))
    detail["last_metric_count"] = int(result.get("metric_count") or 0)
    requests = result.get("requests") if isinstance(result.get("requests"), dict) else {}
    team = requests.get("team") if isinstance(requests.get("team"), dict) else {}
    stat = requests.get("stat_minutes") if isinstance(requests.get("stat_minutes"), dict) else {}
    detail["team_ok"] = bool(team.get("ok"))
    detail["stat_ok"] = bool(stat.get("ok"))
    team_status = team.get("status_code")
    stat_status = stat.get("status_code")
    if team_status is not None:
        detail["team_status"] = team_status
    if stat_status is not None:
        detail["stat_status"] = stat_status

    if not team.get("ok"):
        detail["failure_class"] = f"team_http_{team_status or 'error'}"
    elif not stat.get("ok"):
        detail["failure_class"] = f"stat_http_{stat_status or 'error'}"
    elif result.get("minutes_on") is None or result.get("minutes_off") is None:
        detail["failure_class"] = "minutes_match_missing"
    elif not result.get("metrics"):
        detail["failure_class"] = "team_metrics_empty"
    else:
        detail["failure_class"] = "incomplete_other"
    return detail


def run(batch_size: int, workers: int, request_interval: float) -> dict[str, Any]:
    # v4 calls v3.compact_error dynamically; replace it before collection so the
    # persistent queue never accumulates giant nested error payloads.
    v3.compact_error = compact_failure
    core.request_json = v4.cached_request_json
    summary = v4.run(batch_size, workers, request_interval)

    # v4's in-memory error examples are useful while debugging but expensive to
    # commit repeatedly. Replace them with compact queue records before Git sees them.
    state = v4.load_queue()
    summary["stage2_v5"] = True
    summary["compact_failure_queue"] = True
    summary["batch_error_examples"] = list(state.values())[:10]
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    sample = {"season": "2000-01", "team_id": 1, "player_id": "2", "player_name": "X", "query_start_date": "2000-01-01", "query_end_date": "2000-01-02"}
    out = compact_failure(sample, {"result": {"complete": False, "metric_count": 0, "requests": {"team": {"ok": False, "status_code": 503}}}}, 1)
    assert out["failure_class"] == "team_http_503"
    assert "requests" not in out
    print("run_corrected_off_batch_v5 self-test PASSED")


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
        print(json.dumps(run(args.batch_size, args.workers, args.request_interval), indent=2))


if __name__ == "__main__":
    main()
