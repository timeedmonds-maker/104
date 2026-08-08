from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
CORE = BASE / "impact_database" / "outputs"
ROSTER = BASE / "impact_database" / "roster_tenure"
OUT = BASE / "impact_database" / "corrected_off"
WINDOWS = ROSTER / "player_team_season_windows_evidence_audited.jsonl.gz"
REVIEW = ROSTER / "tenure_review_queue_summary.json"
LONG = OUT / "tenure_segment_on_off.jsonl.gz"
SUMMARY = OUT / "corrected_off_collection_summary.json"
CACHE = OUT / "cache"

TEAM_URL = "https://api.pbpstats.com/get-on-off/nba/team"
STAT_URL = "https://api.pbpstats.com/get-on-off/nba/stat"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"expected object in {path}")
    return data


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def clean_id(v: Any) -> str:
    x = str(v or "").strip()
    if x.endswith(".0") and x[:-2].isdigit():
        x = x[:-2]
    return x if x.isdigit() and x != "0" else ""


def rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    value = payload.get("results")
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def request_json(url: str, params: dict[str, str], attempts: int = 4) -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=(8, 35))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
            return payload, {
                "ok": True, "attempt": attempt, "status_code": response.status_code,
                "elapsed_seconds": round(time.monotonic() - started, 3), "errors": errors,
            }
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc!r}")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    return {}, {"ok": False, "attempt": attempts, "status_code": None, "errors": errors}


def next_day(day: str) -> str:
    return (datetime.fromisoformat(day).date() + timedelta(days=1)).isoformat()


def previous_day(day: str) -> str:
    return (datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()


def query_dates(window: dict[str, Any]) -> tuple[str, str]:
    """Return an inclusive PBP Stats query interval for one exact roster stint.

    Current Stage 1 positive-evidence resolutions prove inclusion of same-day games,
    so transaction dates stay inclusive. Future manual exclusions must either set
    explicit boundary inclusion booleans or pre-adjusted query dates; silent guessing
    is forbidden.
    """
    if window.get("query_start_date") and window.get("query_end_date"):
        return str(window["query_start_date"]), str(window["query_end_date"])
    start, end = str(window["tenure_start"]), str(window["tenure_end"])
    if window.get("start_boundary_included") is False:
        start = next_day(start)
    if window.get("end_boundary_included") is False:
        end = previous_day(end)
    if start > end:
        raise RuntimeError(f"empty effective interval after boundary ordering: {window}")
    excluded = window.get("same_day_excluded_game_ids") or []
    if excluded and "start_boundary_included" not in window and "end_boundary_included" not in window:
        raise RuntimeError("same-day exclusion exists without an explicit effective query boundary")
    return start, end


def stat_minutes(payload: dict[str, Any], player_id: str, player_name: str) -> tuple[float | None, float | None, dict[str, Any] | None]:
    candidates = rows(payload)
    exact = [r for r in candidates if clean_id(r.get("PlayerId") or r.get("EntityId") or r.get("PlayerID")) == player_id]
    if not exact:
        target = player_name.casefold().strip()
        exact = [r for r in candidates if str(r.get("Name") or "").casefold().strip() == target]
    if len(exact) != 1:
        return None, None, None
    r = exact[0]
    return finite(r.get("MinutesOn")), finite(r.get("MinutesOff")), r


def cache_path(window: dict[str, Any], start: str, end: str) -> Path:
    key = f"{window['season']}__{int(window['team_id'])}__{clean_id(window['player_id'])}__{start}__{end}.json.gz"
    return CACHE / key


def collect_window(window: dict[str, Any]) -> dict[str, Any]:
    if window.get("schedule_boundary_status") != "resolved":
        raise RuntimeError(f"attempted Stage 2 collection from unresolved window: {window}")
    season = str(window["season"])
    team_id = int(window["team_id"])
    player_id = clean_id(window.get("player_id"))
    player_name = str(window.get("player") or window.get("player_name") or "").strip()
    if not player_id:
        raise RuntimeError(f"missing player_id: {window}")
    start, end = query_dates(window)
    path = cache_path(window, start, end)
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("complete") is True:
                return cached
        except Exception:
            pass

    common = {
        "Season": season, "SeasonType": "Regular Season", "TeamId": str(team_id),
        "PlayerId": player_id, "FromDate": start, "ToDate": end,
    }
    team_payload, team_meta = request_json(TEAM_URL, common)
    stat_payload, stat_meta = request_json(STAT_URL, {
        "Season": season, "SeasonType": "Regular Season", "TeamId": str(team_id),
        "Stat": "OffRebounds", "FromDate": start, "ToDate": end,
    })
    team_rows = rows(team_payload)
    minutes_on, minutes_off, minute_row = stat_minutes(stat_payload, player_id, player_name)
    metric_rows: list[dict[str, Any]] = []
    for r in team_rows:
        metric = str(r.get("Stat") or "").strip()
        if not metric:
            continue
        metric_rows.append({
            "metric": metric,
            "on": finite(r.get("On")),
            "off_corrected": finite(r.get("Off")),
            "on_minus_off_corrected": finite(r.get("On-Off")),
            "source_extra": {k: v for k, v in r.items() if k not in {"Stat", "On", "Off", "On-Off"}},
        })
    complete = bool(team_meta.get("ok") and stat_meta.get("ok") and metric_rows and minutes_on is not None and minutes_off is not None)
    result = {
        "complete": complete,
        "season": season, "team_id": team_id,
        "team_abbr": window.get("team_abbr"),
        "player_id": player_id, "player": player_name,
        "tenure_start": window.get("tenure_start"), "tenure_end": window.get("tenure_end"),
        "query_start_date": start, "query_end_date": end,
        "team_games_in_window": window.get("team_games_in_window"),
        "tenure_source": window.get("source") or window.get("sources"),
        "tenure_confidence": window.get("confidence") or window.get("tenure_confidence"),
        "boundary_resolution": window.get("same_day_resolution"),
        "minutes_on": minutes_on, "minutes_off": minutes_off,
        "metric_count": len(metric_rows), "metrics": metric_rows,
        "minute_source_row": minute_row,
        "requests": {"team": team_meta, "stat_minutes": stat_meta},
        "generated_utc": now(),
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    tmp.replace(path)
    return result


def build(limit: int | None = None) -> dict[str, Any]:
    review = load_json(REVIEW)
    if review.get("stage1_exact_ready") is not True:
        raise RuntimeError(f"Stage 1 exact-ready gate is false: {review}")
    if int(review.get("review_queue_windows") or 0) != 0:
        raise RuntimeError(f"Stage 1 review queue is not empty: {review}")
    windows = load_rows(WINDOWS)
    exact = [w for w in windows if w.get("schedule_boundary_status") == "resolved"]
    unresolved = [w for w in windows if w.get("schedule_boundary_status") != "resolved"]
    if unresolved:
        raise RuntimeError(f"Stage 1 exact-ready summary conflicts with {len(unresolved)} unresolved windows")

    # Stage 2 is player-impact work: zero-minute roster windows are retained in the
    # tenure audit, but cannot have a player-specific on/off profile and are skipped.
    impact_windows = [w for w in exact if clean_id(w.get("player_id")) and not bool(w.get("zero_minute_only"))]
    if limit is not None:
        impact_windows = impact_windows[:limit]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for i, window in enumerate(impact_windows, 1):
        try:
            result = collect_window(window)
            results.append(result)
            if not result.get("complete"):
                failures.append({"window": window, "result": result})
        except Exception as exc:
            failures.append({"window": window, "error": repr(exc)})
        if i % 100 == 0 or i == len(impact_windows):
            print(f"corrected OFF tenure windows {i}/{len(impact_windows)} failures={len(failures)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(LONG, "wt", encoding="utf-8") as handle:
        for result in results:
            if not result.get("complete"):
                continue
            base = {k: v for k, v in result.items() if k not in {"metrics", "minute_source_row", "requests"}}
            for metric in result["metrics"]:
                handle.write(json.dumps({**base, **metric}, ensure_ascii=False) + "\n")
    summary = {
        "generated_utc": now(),
        "stage1_exact_ready": True,
        "input_windows": len(windows),
        "impact_windows_requested": len(impact_windows),
        "complete_windows": sum(bool(r.get("complete")) for r in results),
        "failed_windows": len(failures),
        "metric_rows": sum(len(r.get("metrics") or []) for r in results if r.get("complete")),
        "output": str(LONG),
        "cache": str(CACHE),
        "failures": failures[:200],
        "policy": "tenure-scoped PBP Stats player/team on-off profiles only; no original 780 team-season rerun; no teammate-pair analysis",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if failures:
        raise RuntimeError(f"corrected OFF collection incomplete: {len(failures)} failed windows")
    return summary


def self_test() -> None:
    base = {"season": "2023-24", "team_id": 10, "player_id": "123", "tenure_start": "2024-02-01", "tenure_end": "2024-02-10"}
    assert query_dates(base) == ("2024-02-01", "2024-02-10")
    assert query_dates({**base, "start_boundary_included": False})[0] == "2024-02-02"
    assert query_dates({**base, "end_boundary_included": False})[1] == "2024-02-09"
    payload = {"results": [{"Name": "Test Player", "MinutesOn": 12.5, "MinutesOff": 35.0}]}
    a, b, row = stat_minutes(payload, "123", "Test Player")
    assert a == 12.5 and b == 35.0 and row is not None
    profile = rows({"results": [{"Stat": "OffRating", "On": 120, "Off": 110, "On-Off": 10}]})
    assert profile[0]["Stat"] == "OffRating"
    print("build_corrected_tenure_off self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(build(args.limit), indent=2))


if __name__ == "__main__":
    main()
