from __future__ import annotations

import argparse
import gzip
import json
import math
import unicodedata
from typing import Any

import run_corrected_off_batch_v5 as v5

v4 = v5.v4
v3 = v4.v3
batch = v4.batch
core = v4.core

TOTALS_URL = "https://api.pbpstats.com/get-totals/nba"


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def totals_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("multi_row_table_data")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def row_player_id(row: dict[str, Any]) -> str:
    return core.clean_id(row.get("EntityId") or row.get("RowId") or row.get("PlayerId") or row.get("PlayerID"))


def row_name(row: dict[str, Any]) -> str:
    return str(row.get("Name") or row.get("ShortName") or "").strip()


def derive_minutes_from_totals(payload: Any, player_id: str, player_name: str):
    rows = totals_rows(payload)
    if not rows:
        return None, None, None, None

    seconds_rows: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        seconds = core.finite(row.get("SecondsPlayed"))
        if seconds is None or seconds < 0:
            continue
        seconds_rows.append((row, seconds))
    if not seconds_rows:
        return None, None, None, None

    focal = [item for item in seconds_rows if row_player_id(item[0]) == player_id]
    if not focal:
        target = norm(player_name)
        focal = [item for item in seconds_rows if norm(row_name(item[0])) == target]
    if len(focal) != 1:
        return None, None, None, None

    focal_row, focal_seconds = focal[0]
    total_player_seconds = sum(seconds for _, seconds in seconds_rows)
    if total_player_seconds <= 0:
        return None, None, None, None

    # Five players are on court for a team at every game-clock second. Summing
    # all player seconds and dividing by five therefore gives exact team court
    # time, including overtime, for the date-filtered interval.
    team_court_seconds = total_player_seconds / 5.0
    off_seconds = team_court_seconds - focal_seconds
    if off_seconds < -0.01:
        return None, None, None, None
    off_seconds = max(0.0, off_seconds)

    return focal_seconds / 60.0, off_seconds / 60.0, focal_row, {
        "team_player_rows": len(seconds_rows),
        "total_player_seconds": total_player_seconds,
        "team_court_seconds": team_court_seconds,
    }


def collect_window_v8(window: dict[str, Any]) -> dict[str, Any]:
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

    team_params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "TeamId": str(team_id),
        "PlayerId": player_id,
        "FromDate": start,
        "ToDate": end,
    }
    team_payload, team_meta = v4.cached_request_json(core.TEAM_URL, team_params)
    team_rows = core.rows(team_payload)

    if team_meta.get("ok") and team_rows:
        # Supported documented endpoint: one date-filtered TeamId+Type=Player totals
        # response contains SecondsPlayed for every team player in this interval.
        # The payload is player-independent, so v4's endpoint cache automatically
        # shares it across any windows with the same team/date range.
        totals_params = {
            "Season": season,
            "SeasonType": "Regular Season",
            "Type": "Player",
            "TeamId": str(team_id),
            "FromDate": start,
            "ToDate": end,
        }
        totals_payload, totals_meta = v4.cached_request_json(TOTALS_URL, totals_params)
        minutes_on, minutes_off, minute_row, totals_detail = derive_minutes_from_totals(
            totals_payload, player_id, player_name
        )
    else:
        totals_meta = {"ok": False, "skipped": True, "reason": "team profile unavailable or empty"}
        minutes_on = minutes_off = None
        minute_row = None
        totals_detail = None

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

    # Keep v5's existing compact failure classifier compatible by exposing the
    # totals-minute request under stat_minutes as an alias as well.
    complete = bool(
        team_meta.get("ok")
        and totals_meta.get("ok")
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
        "minutes_source": "date_filtered_player_totals_seconds_sum" if minutes_on is not None else None,
        "totals_minutes_detail": totals_detail,
        "metric_count": len(metric_rows),
        "metrics": metric_rows,
        "minute_source_row": minute_row,
        "requests": {
            "team": team_meta,
            "totals_minutes": totals_meta,
            "stat_minutes": totals_meta,
        },
        "collection_source": "player_scoped_team_profile_plus_date_filtered_totals_v8",
        "generated_utc": core.now(),
    }

    core.CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    tmp.replace(path)
    return result


def run(batch_size: int = 200, workers: int = 1, request_interval: float = 0.50) -> dict[str, Any]:
    core.collect_window = collect_window_v8
    core.request_json = v4.cached_request_json
    summary = v5.run(batch_size, workers, request_interval)
    summary["stage2_v8"] = True
    summary["minutes_optimization"] = (
        "date-filtered get-totals Type=Player + TeamId; focal SecondsPlayed gives MinutesOn and "
        "sum(all team player SecondsPlayed)/5 minus focal gives MinutesOff; response shared by identical team/date intervals"
    )
    counters = summary.get("network_counters") if isinstance(summary.get("network_counters"), dict) else {}
    successes = int(summary.get("batch_successes") or 0)
    summary["network_requests_per_batch_success"] = (
        float(counters.get("network_requests") or 0) / successes if successes else None
    )
    core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    payload = {
        "multi_row_table_data": [
            {"EntityId": "1", "Name": "A", "SecondsPlayed": 600},
            {"EntityId": "2", "Name": "B", "SecondsPlayed": 600},
            {"EntityId": "3", "Name": "C", "SecondsPlayed": 600},
            {"EntityId": "4", "Name": "D", "SecondsPlayed": 600},
            {"EntityId": "5", "Name": "E", "SecondsPlayed": 600},
            {"EntityId": "6", "Name": "F", "SecondsPlayed": 300},
            {"EntityId": "1", "Name": "A duplicate impossible", "SecondsPlayed": 0},
        ]
    }
    # Remove duplicate zero row for a valid simple check.
    payload["multi_row_table_data"].pop()
    on, off, row, detail = derive_minutes_from_totals(payload, "1", "A")
    assert on == 10.0
    assert abs(off - 40.0) < 1e-9
    assert row is not None and detail is not None
    print("run_corrected_off_batch_v8 self-test PASSED")


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
