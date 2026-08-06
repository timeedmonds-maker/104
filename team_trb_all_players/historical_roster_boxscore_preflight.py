from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "historical_roster_boxscore_preflight.json"

SEASON = "2000-01"
SEASON_TYPE = "Regular Season"
TRADE_DATE = date(2001, 2, 22)
PLAYER_ID = "87"
PLAYER_NAME = "Dikembe Mutombo"
ATL = 1610612737
PHI = 1610612755

GAMES_URL = "https://api.pbpstats.com/get-games/nba"
BOX_SUMMARY_URL = "https://stats.nba.com/stats/boxscoresummaryv2"
BOX_TRADITIONAL_URL = "https://stats.nba.com/stats/boxscoretraditionalv2"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
}


def log(message: str) -> None:
    print(message, flush=True)


def request_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(2):
        try:
            log(f"GET {url} attempt {attempt + 1}/2")
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=(10, 20),
            )
            log(f"  status={response.status_code} bytes={len(response.content)}")
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            errors.append(repr(exc))
            if attempt == 0:
                time.sleep(2)
    raise RuntimeError(f"request failed {url}: {'; '.join(errors)}")


def as_date(value: Any) -> date:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def result_rows(payload: dict[str, Any], result_name: str) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets", [])
    if isinstance(result_sets, dict):
        result_sets = [result_sets]
    for result_set in result_sets:
        if result_set.get("name") == result_name:
            headers = result_set.get("headers", [])
            return [dict(zip(headers, row)) for row in result_set.get("rowSet", [])]
    return []


def player_matches(row: dict[str, Any], team_id: int) -> bool:
    row_team_id = str(row.get("TEAM_ID") or "")
    row_player_id = str(row.get("PLAYER_ID") or "")
    row_name = str(
        row.get("PLAYER_NAME")
        or f"{row.get('FIRST_NAME', '')} {row.get('LAST_NAME', '')}"
    ).strip()
    return row_team_id == str(team_id) and (
        row_player_id == PLAYER_ID or row_name.casefold() == PLAYER_NAME.casefold()
    )


def roster_evidence(game_id: str, team_id: int) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    errors: list[str] = []

    calls = [
        (BOX_TRADITIONAL_URL, {
            "GameID": game_id,
            "StartPeriod": "1",
            "EndPeriod": "10",
            "StartRange": "0",
            "EndRange": "0",
            "RangeType": "0",
        }, "PlayerStats", "active_or_dnp"),
        (BOX_SUMMARY_URL, {"GameID": game_id}, "InactivePlayers", "inactive"),
    ]

    roster_row_count = 0
    for url, params, result_name, roster_status in calls:
        try:
            payload = request_json(url, params)
            rows = result_rows(payload, result_name)
            roster_row_count += sum(
                1 for row in rows if str(row.get("TEAM_ID") or "") == str(team_id)
            )
            for row in rows:
                if player_matches(row, team_id):
                    evidence.append({
                        "endpoint": url,
                        "result_set": result_name,
                        "roster_status": roster_status,
                        "row": row,
                    })
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")

    return {
        "game_id": game_id,
        "team_id": team_id,
        "player_rostered": bool(evidence),
        "team_roster_rows_returned": roster_row_count,
        "evidence": evidence,
        "errors": errors,
    }


def main() -> int:
    log("Fetching 2000-01 game list from PBPStats")
    games_payload = request_json(
        GAMES_URL,
        {"Season": SEASON, "SeasonType": SEASON_TYPE},
    )
    games = []
    for row in games_payload.get("results", []):
        row = dict(row)
        row["_date"] = as_date(row["Date"])
        row["_teams"] = {
            int(row["HomeTeamId"]),
            int(row["AwayTeamId"]),
        }
        games.append(row)

    def team_games(team_id: int) -> list[dict[str, Any]]:
        return sorted(
            [game for game in games if team_id in game["_teams"]],
            key=lambda game: (game["_date"], game["GameId"]),
        )

    atl_games = team_games(ATL)
    phi_games = team_games(PHI)

    checks = {
        "atl_before": max(
            (game for game in atl_games if game["_date"] < TRADE_DATE),
            key=lambda game: game["_date"],
        ),
        "atl_after": min(
            (game for game in atl_games if game["_date"] > TRADE_DATE),
            key=lambda game: game["_date"],
        ),
        "phi_before": max(
            (game for game in phi_games if game["_date"] < TRADE_DATE),
            key=lambda game: game["_date"],
        ),
        "phi_after": min(
            (game for game in phi_games if game["_date"] > TRADE_DATE),
            key=lambda game: game["_date"],
        ),
    }

    expected = {
        "atl_before": True,
        "atl_after": False,
        "phi_before": False,
        "phi_after": True,
    }
    team_for_check = {
        "atl_before": ATL,
        "atl_after": ATL,
        "phi_before": PHI,
        "phi_after": PHI,
    }

    results: dict[str, Any] = {}
    for name, game in checks.items():
        log(f"Checking {name}: {game['Date']} game {game['GameId']}")
        evidence = roster_evidence(str(game["GameId"]), team_for_check[name])
        evidence.update({
            "date": game["Date"],
            "expected_player_rostered": expected[name],
            "matches_expected": evidence["player_rostered"] == expected[name],
        })
        results[name] = evidence

    endpoint_reachable = all(not result["errors"] for result in results.values())
    historical_roster_rows_available = all(
        result["team_roster_rows_returned"] > 0 for result in results.values()
    )
    boundary_matches = all(result["matches_expected"] for result in results.values())

    report = {
        "purpose": (
            "Test whether official NBA historical box scores can reconstruct game-by-game "
            "roster membership without third-party transaction scraping."
        ),
        "season": SEASON,
        "player_id": PLAYER_ID,
        "player": PLAYER_NAME,
        "known_trade_date": TRADE_DATE.isoformat(),
        "results": results,
        "endpoint_reachable": endpoint_reachable,
        "historical_roster_rows_available": historical_roster_rows_available,
        "boundary_matches": boundary_matches,
        "preflight_passed": (
            endpoint_reachable
            and historical_roster_rows_available
            and boundary_matches
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(json.dumps({
        "preflight_passed": report["preflight_passed"],
        "endpoint_reachable": endpoint_reachable,
        "historical_roster_rows_available": historical_roster_rows_available,
        "boundary_matches": boundary_matches,
        "checks": {
            name: {
                "date": result["date"],
                "game_id": result["game_id"],
                "expected": result["expected_player_rostered"],
                "observed": result["player_rostered"],
                "roster_rows": result["team_roster_rows_returned"],
                "errors": result["errors"],
            }
            for name, result in results.items()
        },
        "report": str(OUT),
    }, indent=2))
    return 0 if report["preflight_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
