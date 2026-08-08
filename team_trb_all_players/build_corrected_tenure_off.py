from __future__ import annotations

import argparse
import gzip
import json
import math
import threading
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
_THREAD_LOCAL = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_session() -> requests.Session:
    """Return one persistent HTTP session per worker thread."""
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _THREAD_LOCAL.session = session
    return session


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
    session = http_session()
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = session.get(url, params=params, timeout=(8, 35))
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
    """Return the inclusive effective interval for one deterministic roster stint.

    Stage 1 now applies a blanket transaction-day convention: the old/departing team
    includes the transaction date and the new/incoming team begins the next day. Explicit
    query dates produced by Stage 1 are authoritative, but are still validated here.
    """
    if window.get("query_start_date") and window.get("query_end_date"):
        start = str(window["query_start_date"])
        end = str(window["query_end_date"])
        if start > end:
            raise RuntimeError(f"empty effective interval after transaction-day policy: {window}")
        return start, end
    start, end = str(window["tenure_start"]), str(window["tenure_end"])
    if window.get("start_boundary_included") is False:
        start = next_day(start)
    if window.get("end_boundary_included") is False:
        end = previous_day(end)
    if start > end:
        raise RuntimeError(f"empty effective interval after boundary ordering: {window}")
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
    if bool(window.get("zero_game_window")):
        raise RuntimeError(f"attempted Stage 2 collection from zero-game tenure window: {window}")
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
    team_rows = rows(team_payload)

    if team_meta.get("ok") and team_rows:
        stat_payload, stat_meta = request_json(STAT_URL, {
            "Season": season, "SeasonType": "Regular Season", "TeamId": str(team_id),
            "Stat": "OffRebounds", "FromDate": start, "ToDate": end,
        })
        minutes_on, minutes_off, minute_row = stat_minutes(stat_payload, player_id, player_name)
    else:
        stat_payload = {}
        stat_meta = {"ok": False, "skipped": True, "reason": "team profile unavailable or empty"}
        minutes_on = minutes_off = None
        minute_row = None

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
        "transaction_day_policy": window.get("transaction_day_policy"),
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

    impact_windows = [
        w for w in exact
        if clean_id(w.get("player_id"))
        and not bool(w.get("zero_minute_only"))
        and not bool(w.get("zero_game_window"))
    ]
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
        "zero_game_windows_skipped": sum(bool(w.get("zero_game_window")) for w in exact),
        "impact_windows_requested": len(impact_windows),
        "complete_windows": sum(bool(r.get("complete")) for r in results),
        "failed_windows": len(failures),
        "metric_rows": sum(len(r.get("metrics") or []) for r in results if r.get("complete")),
        "output": str(LONG),
        "cache": str(CACHE),
        "failures": failures[:200],
        "policy": "tenure-scoped PBP Stats profiles using deterministic transaction-day roster windows; core never rerun; teammate pairs excluded",
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
    assert query_dates({**base, "query_start_date": "2024-02-02", "query_end_date": "2024-02-10"}) == ("2024-02-02", "2024-02-10")
    try:
        query_dates({**base, "query_start_date": "2024-02-11", "query_end_date": "2024-02-10"})
        raise AssertionError("invalid explicit effective interval should fail")
    except RuntimeError:
        pass
    payload = {"results": [{"Name": "Test Player", "MinutesOn": 12.5, "MinutesOff": 35.0}]}
    a, b, row = stat_minutes(payload, "123", "Test Player")
    assert a == 12.5 and b == 35.0 and row is not None
    profile = rows({"results": [{"Stat": "OffRating", "On": 120, "Off": 110, "On-Off": 10}]})
    assert profile[0]["Stat"] == "OffRating"
    assert http_session() is http_session()
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
