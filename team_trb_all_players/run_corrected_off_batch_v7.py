from __future__ import annotations

import argparse
import gzip
import json
from typing import Any

import run_corrected_off_batch_v5 as v5

v4 = v5.v4
v3 = v4.v3
batch = v4.batch
core = v4.core


def team_profile_minutes(team_rows: list[dict[str, Any]]) -> tuple[float | None, float | None, dict[str, Any] | None]:
    """Extract MinutesOn/MinutesOff directly from player-scoped TEAM rows when present.

    The values must be internally consistent across every metric row that exposes them.
    Any ambiguity falls back to the existing STAT endpoint rather than weakening QA.
    """
    pairs: set[tuple[float, float]] = set()
    source: dict[str, Any] | None = None
    for row in team_rows:
        on = core.finite(row.get("MinutesOn"))
        off = core.finite(row.get("MinutesOff"))
        if on is None or off is None:
            continue
        pairs.add((on, off))
        if source is None:
            source = row
    if len(pairs) != 1:
        return None, None, None
    on, off = next(iter(pairs))
    return on, off, source


def collect_window_v7(window: dict[str, Any]) -> dict[str, Any]:
    if window.get("schedule_boundary_status") != "resolved":
        raise RuntimeError(f"attempted Stage 2 collection from unresolved window: {window}")
    if bool(window.get("zero_game_window")):
        raise RuntimeError(f"attempted Stage 2 collection from zero-game tenure window: {window}")

    season = str(window["season"])
    team_id = int(window["team_id"])
    player_id = core.clean_id(window.get("player_id"))
    player_name = str(window.get("player") or window.get("player_name") or "").strip()
    if not player_id:
        raise RuntimeError(f"missing player_id: {window}")
    start, end = core.query_dates(window)
    path = core.cache_path(window, start, end)
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("complete") is True:
                return cached
        except Exception:
            pass

    common = {
        "Season": season,
        "SeasonType": "Regular Season",
        "TeamId": str(team_id),
        "PlayerId": player_id,
        "FromDate": start,
        "ToDate": end,
    }

    # Keep the player-scoped TEAM endpoint that has already produced the 89-metric
    # Stage2 profile successfully. v4's persistent endpoint cache means previously
    # successful TEAM payloads are reused with zero network calls.
    team_payload, team_meta = v4.cached_request_json(core.TEAM_URL, common)
    team_rows = core.rows(team_payload)

    minutes_source = None
    minute_row = None
    if team_meta.get("ok") and team_rows:
        minutes_on, minutes_off, minute_row = team_profile_minutes(team_rows)
        if minutes_on is not None and minutes_off is not None:
            # Critical v7 optimization: no second API call.
            stat_meta = {
                "ok": True,
                "skipped": True,
                "reason": "MinutesOn/MinutesOff read directly from player-scoped TEAM metric rows",
                "status_code": None,
            }
            minutes_source = "team_profile_rows"
        else:
            # Exactness-preserving fallback. Only used where TEAM rows do not expose
            # an unambiguous minute pair; successful STAT payloads remain cached/shared.
            stat_payload, stat_meta = v4.cached_request_json(core.STAT_URL, {
                "Season": season,
                "SeasonType": "Regular Season",
                "TeamId": str(team_id),
                "Stat": "OffRebounds",
                "FromDate": start,
                "ToDate": end,
            })
            minutes_on, minutes_off, minute_row = v3.robust_stat_minutes(stat_payload, player_id, player_name)
            minutes_source = "stat_profile" if minutes_on is not None and minutes_off is not None else None
    else:
        stat_meta = {"ok": False, "skipped": True, "reason": "team profile unavailable or empty"}
        minutes_on = minutes_off = None

    metric_rows: list[dict[str, Any]] = []
    for row in team_rows:
        metric = str(row.get("Stat") or "").strip()
        if not metric:
            continue
        metric_rows.append({
            "metric": metric,
            "on": core.finite(row.get("On")),
            "off_corrected": core.finite(row.get("Off")),
            "on_minus_off_corrected": core.finite(row.get("On-Off")),
            "source_extra": {k: v for k, v in row.items() if k not in {"Stat", "On", "Off", "On-Off"}},
        })

    complete = bool(
        team_meta.get("ok")
        and stat_meta.get("ok")
        and metric_rows
        and minutes_on is not None
        and minutes_off is not None
    )
    result = {
        "complete": complete,
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
        "minutes_source": minutes_source,
        "metric_count": len(metric_rows),
        "metrics": metric_rows,
        "minute_source_row": minute_row,
        "requests": {"team": team_meta, "stat_minutes": stat_meta},
        "collection_source": "player_scoped_team_profile_v7",
        "generated_utc": core.now(),
    }

    core.CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    tmp.replace(path)
    return result


def run(batch_size: int = 200, workers: int = 1, request_interval: float = 0.50) -> dict[str, Any]:
    # v5 retains fresh-first quarantine, persistent endpoint caching, compact queue,
    # adaptive concurrency, completed-core reuse and exact completion gates.
    core.collect_window = collect_window_v7
    core.request_json = v4.cached_request_json
    summary = v5.run(batch_size, workers, request_interval)
    summary["stage2_v7"] = True
    summary["minutes_optimization"] = "player-scoped TEAM rows first; STAT endpoint only as exactness-preserving fallback"
    counters = summary.get("network_counters") if isinstance(summary.get("network_counters"), dict) else {}
    summary["network_requests_per_batch_success"] = (
        float(counters.get("network_requests") or 0) / float(summary.get("batch_successes") or 1)
        if int(summary.get("batch_successes") or 0) > 0 else None
    )
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    rows = [
        {"Stat": "A", "On": 1, "Off": 2, "On-Off": -1, "MinutesOn": 12.5, "MinutesOff": 35.0},
        {"Stat": "B", "On": 3, "Off": 4, "On-Off": -1, "MinutesOn": 12.5, "MinutesOff": 35.0},
    ]
    on, off, source = team_profile_minutes(rows)
    assert on == 12.5 and off == 35.0 and source is not None
    bad = rows + [{"Stat": "C", "MinutesOn": 13.0, "MinutesOff": 35.0}]
    assert team_profile_minutes(bad) == (None, None, None)
    print("run_corrected_off_batch_v7 self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=200)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--request-interval", type=float, default=0.50)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(run(args.batch_size, args.workers, args.request_interval), indent=2))


if __name__ == "__main__":
    main()
