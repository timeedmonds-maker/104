from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
ROSTER = IMPACT / "roster_tenure"
OUT = ROSTER / "regular_season_games.jsonl.gz"
SUMMARY = ROSTER / "regular_season_games_summary.json"
RAW = ROSTER / "regular_season_games_raw"

URL = "https://api.pbpstats.com/get-games/nba"
SEASONS = [f"{year}-{str(year + 1)[-2:]}" for year in range(2000, 2026)]
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}
_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)


def request_json(params: dict[str, str], attempts: int = 6) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            response = _SESSION.get(URL, params=params, timeout=(10, 90))
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
            return payload
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < attempts:
                time.sleep(min(15.0, 1.5 * attempt))
    raise RuntimeError("get-games failed: " + " | ".join(errors))


def rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "Games", "games", "multi_row_table_data", "data"):
        rows = payload.get(key)
        if isinstance(rows, list) and (not rows or isinstance(rows[0], dict)):
            return rows
    for value in payload.values():
        if isinstance(value, dict):
            rows = rows_from_payload(value)
            if rows:
                return rows
    return []


def first(row: dict[str, Any], *keys: str) -> Any:
    lookup = {str(k).casefold(): v for k, v in row.items()}
    for key in keys:
        if key.casefold() in lookup:
            return lookup[key.casefold()]
    return None


def clean_team_id(value: Any) -> int | None:
    try:
        value = int(str(value).strip())
    except Exception:
        return None
    return value if value > 0 else None


def clean_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:10]
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_game(row: dict[str, Any], season: str) -> dict[str, Any] | None:
    game_id = str(first(row, "GameId", "GameID", "game_id", "id") or "").strip()
    game_date = clean_date(first(row, "Date", "GameDate", "GameDateTime", "StartTime", "date"))
    home = clean_team_id(first(row, "HomeTeamId", "HomeTeamID", "home_team_id", "HomeTeam"))
    away = clean_team_id(first(row, "AwayTeamId", "AwayTeamID", "VisitorTeamId", "visitor_team_id", "away_team_id", "AwayTeam"))
    if not game_id or not game_date or not home or not away:
        return None
    return {
        "season": season,
        "game_id": game_id,
        "game_date": game_date,
        "home_team_id": home,
        "away_team_id": away,
        "source_system": "PBP Stats get-games",
        "source_reference": f"{URL}?Season={season}&SeasonType=Regular+Season",
    }


def normalize_payload(payload: dict[str, Any], season: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_rows = rows_from_payload(payload)
    games = [game for row in source_rows if (game := normalize_game(row, season))]
    unique = {(g["game_id"], g["game_date"]): g for g in games}
    games = sorted(unique.values(), key=lambda g: (g["game_date"], g["game_id"]))
    detail = {
        "season": season,
        "source_rows": len(source_rows),
        "normalized_games": len(games),
        "dropped_rows": len(source_rows) - len(games),
    }
    return games, detail


def load_cached_season(season: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    path = RAW / f"{season}.json.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        games, detail = normalize_payload(payload, season)
        if not (59 <= len(games) <= 1230):
            return None
        detail["cache"] = "hit"
        return games, detail
    except Exception:
        return None


def fetch_season(season: str, *, force: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not force:
        cached = load_cached_season(season)
        if cached is not None:
            return cached
    payload = request_json({"Season": season, "SeasonType": "Regular Season"})
    RAW.mkdir(parents=True, exist_ok=True)
    with gzip.open(RAW / f"{season}.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    games, detail = normalize_payload(payload, season)
    detail["cache"] = "miss"
    return games, detail


def build(*, force: bool = False) -> dict[str, Any]:
    ROSTER.mkdir(parents=True, exist_ok=True)
    all_games: list[dict[str, Any]] = []
    per_season: list[dict[str, Any]] = []
    cache_hits = 0
    started = time.monotonic()
    for season in SEASONS:
        games, detail = fetch_season(season, force=force)
        if not (59 <= len(games) <= 1230):
            raise RuntimeError(f"{season}: implausible regular-season game count {len(games)}")
        all_games.extend(games)
        per_season.append(detail)
        cache_hits += int(detail.get("cache") == "hit")
        print(season, len(games), "games", detail.get("cache"), flush=True)

    with gzip.open(OUT, "wt", encoding="utf-8") as handle:
        for game in all_games:
            handle.write(json.dumps(game, ensure_ascii=False) + "\n")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": URL,
        "season_type": "Regular Season",
        "seasons": SEASONS,
        "game_count": len(all_games),
        "cache_hits": cache_hits,
        "network_fetches": len(SEASONS) - cache_hits,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "per_season": per_season,
        "output": str(OUT),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    sample = {
        "GameId": "0022300001",
        "Date": "2023-10-24",
        "HomeTeamId": 1610612743,
        "AwayTeamId": 1610612747,
    }
    game = normalize_game(sample, "2023-24")
    assert game and game["game_date"] == "2023-10-24"
    assert game["home_team_id"] == 1610612743
    assert game["away_team_id"] == 1610612747
    nested = {"results": [sample]}
    assert len(rows_from_payload(nested)) == 1
    games, detail = normalize_payload(nested, "2023-24")
    assert len(games) == 1 and detail["normalized_games"] == 1
    print("fetch_regular_season_games self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    summary = build(force=args.force)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
