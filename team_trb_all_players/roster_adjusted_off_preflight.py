from __future__ import annotations

import json
import math
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "roster_adjusted_off_preflight.json"
SEASON = "2023-24"
SEASON_TYPE = "Regular Season"
PLAYER_ID = "203500"
PLAYER_NAME = "Steven Adams"
MEM, HOU = 1610612763, 1610612745

MOVEMENT_URLS = [
    "https://stats.nba.com/js/data/playermovement/NBA_Player_Movement.json",
    "https://www.nba.com/stats/js/data/playermovement/NBA_Player_Movement.json",
]
REALGM_TX = "https://basketball.realgm.com/nba/transactions/league/2024"
REALGM_ROSTER = "https://basketball.realgm.com/nba/teams/Memphis-Grizzlies/14/Rosters/Regular/2024"
GAMES_URL = "https://api.pbpstats.com/get-games/nba"
TOTALS_URL = "https://api.pbpstats.com/get-totals/nba"
WOWY_URL = "https://api.pbpstats.com/get-wowy-stats/nba"
BOX_SUMMARY_URL = "https://stats.nba.com/stats/boxscoresummaryv2"
BOX_TRADITIONAL_URL = "https://stats.nba.com/stats/boxscoretraditionalv2"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
}


def get(url: str, params: dict[str, str] | None = None, *, text: bool = False) -> Any:
    errors: list[str] = []
    for attempt in range(5):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=(15, 120))
            response.raise_for_status()
            return response.text if text else response.json()
        except Exception as exc:
            errors.append(repr(exc))
            if attempt < 4:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"request failed {url}: {'; '.join(errors)}")


def as_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            pass
    return None


def movement_rows(payload: Any) -> list[dict[str, Any]]:
    root = payload.get("NBA_Player_Movement", {}) if isinstance(payload, dict) else {}
    rows = root.get("rows", [])
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    columns = []
    for column in root.get("columns", root.get("headers", [])):
        columns.append(
            column if isinstance(column, str) else column.get("name", column.get("Name", ""))
        )
    return [dict(zip(columns, row)) for row in rows if isinstance(row, list)]


def ci(row: dict[str, Any], *keys: str) -> Any:
    lookup = {str(key).casefold(): value for key, value in row.items()}
    for key in keys:
        if key.casefold() in lookup:
            return lookup[key.casefold()]
    return None


def official_trade_date() -> tuple[date | None, dict[str, Any]]:
    report: dict[str, Any] = {"errors": []}
    for url in MOVEMENT_URLS:
        try:
            rows = movement_rows(get(url))
            if not rows:
                report["errors"].append(f"{url}: zero rows")
                continue
            dates = [
                parsed
                for parsed in (as_date(ci(row, "TRANSACTION_DATE", "Date")) for row in rows)
                if parsed
            ]
            adams = []
            for row in rows:
                player_id = str(ci(row, "PLAYER_ID", "PlayerId") or "")
                description = str(ci(row, "TRANSACTION_DESCRIPTION", "Description") or "")
                slug = str(ci(row, "PLAYER_SLUG", "PlayerSlug") or "")
                if (
                    player_id == PLAYER_ID
                    or "steven-adams" in slug.casefold()
                    or PLAYER_NAME.casefold() in description.casefold()
                ):
                    adams.append(row)
            candidates = []
            for row in adams:
                transaction_date = as_date(ci(row, "TRANSACTION_DATE", "Date"))
                description = str(
                    ci(row, "TRANSACTION_DESCRIPTION", "Description") or ""
                ).casefold()
                if (
                    transaction_date
                    and transaction_date.year == 2024
                    and "houston" in description
                    and "memphis" in description
                ):
                    candidates.append(transaction_date)
            report.update(
                {
                    "source": url,
                    "row_count": len(rows),
                    "minimum_date": min(dates).isoformat() if dates else None,
                    "maximum_date": max(dates).isoformat() if dates else None,
                    "adams_rows": adams,
                }
            )
            if candidates:
                return min(candidates), report
        except Exception as exc:
            report["errors"].append(f"{url}: {exc!r}")

    html = get(REALGM_TX, text=True)
    plain = re.sub(r"<[^>]+>", "\n", html)
    index = plain.casefold().find("steven adams")
    dates = list(
        re.finditer(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+2024\b",
            plain[:index],
            re.I,
        )
    )
    transaction_date = as_date(dates[-1].group(0)) if index >= 0 and dates else None
    report["fallback"] = REALGM_TX
    return transaction_date, report


def result_rows(payload: Any, name: str) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets", []) if isinstance(payload, dict) else []
    if isinstance(result_sets, dict):
        result_sets = [result_sets]
    for result_set in result_sets:
        if isinstance(result_set, dict) and result_set.get("name") == name:
            headers = result_set.get("headers", [])
            rows = result_set.get("rowSet", [])
            return [dict(zip(headers, row)) for row in rows]
    return []


def rostered_on_game(game_id: str, team_id: int) -> dict[str, Any]:
    evidence, errors = [], []
    calls = [
        (BOX_SUMMARY_URL, {"GameID": game_id}, "InactivePlayers"),
        (
            BOX_TRADITIONAL_URL,
            {
                "GameID": game_id,
                "StartPeriod": "1",
                "EndPeriod": "10",
                "StartRange": "0",
                "EndRange": "0",
                "RangeType": "0",
            },
            "PlayerStats",
        ),
    ]
    for url, params, name in calls:
        try:
            for row in result_rows(get(url, params), name):
                player_id = str(row.get("PLAYER_ID") or "")
                row_team_id = str(row.get("TEAM_ID") or "")
                player_name = str(
                    row.get("PLAYER_NAME")
                    or f"{row.get('FIRST_NAME', '')} {row.get('LAST_NAME', '')}"
                ).strip()
                if row_team_id == str(team_id) and (
                    player_id == PLAYER_ID or player_name.casefold() == PLAYER_NAME.casefold()
                ):
                    evidence.append({"endpoint": url, "result_set": name, "row": row})
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    return {
        "game_id": game_id,
        "team_id": team_id,
        "rostered": bool(evidence),
        "evidence": evidence,
        "errors": errors,
    }


def fetch_games() -> list[dict[str, Any]]:
    rows = get(GAMES_URL, {"Season": SEASON, "SeasonType": SEASON_TYPE}).get("results", [])
    output = []
    for row in rows:
        game_date = as_date(row.get("Date"))
        if game_date:
            output.append({**row, "_date": game_date})
    return sorted(output, key=lambda row: (row["_date"], str(row.get("GameId", ""))))


def team_games(games: list[dict[str, Any]], team_id: int) -> list[dict[str, Any]]:
    return [
        game
        for game in games
        if team_id in {int(game.get("HomeTeamId", 0)), int(game.get("AwayTeamId", 0))}
    ]


def window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("empty roster window")
    rows = sorted(rows, key=lambda row: (row["_date"], str(row.get("GameId", ""))))
    return {
        "from_date": rows[0]["_date"].isoformat(),
        "to_date": rows[-1]["_date"].isoformat(),
        "games": len(rows),
        "game_ids": [str(row.get("GameId", "")) for row in rows],
    }


def build_windows(games: list[dict[str, Any]], trade: date) -> dict[str, Any]:
    memphis_all, houston_all = team_games(games, MEM), team_games(games, HOU)
    memphis_rows = [game for game in memphis_all if game["_date"] < trade]
    houston_rows = [game for game in houston_all if game["_date"] > trade]
    resolution = []

    for game in games:
        if game["_date"] != trade:
            continue
        participants = {int(game.get("HomeTeamId", 0)), int(game.get("AwayTeamId", 0))}
        for team_id, destination in ((MEM, memphis_rows), (HOU, houston_rows)):
            if team_id in participants:
                check = rostered_on_game(str(game.get("GameId")), team_id)
                resolution.append(check)
                if check["rostered"]:
                    destination.append(game)

    same_day = [
        game
        for game in games
        if game["_date"] == trade
        and {MEM, HOU}
        & {int(game.get("HomeTeamId", 0)), int(game.get("AwayTeamId", 0))}
    ]
    if same_day and not any(item["rostered"] for item in resolution):
        raise RuntimeError(
            "same-day transaction game could not be resolved from official box-score rosters"
        )
    return {
        "trade_date": trade.isoformat(),
        "same_day_resolution": resolution,
        "memphis": window(memphis_rows),
        "houston": window(houston_rows),
        "memphis_full_games": len(memphis_all),
        "houston_full_games": len(houston_all),
    }


def totals_row(payload: Any, team_id: int) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("single_row_table_data"), dict):
        return payload["single_row_table_data"]
    rows = payload.get("multi_row_table_data", []) if isinstance(payload, dict) else []
    for row in rows:
        if str(row.get("EntityId") or row.get("TeamId") or "") == str(team_id):
            return row
    return rows[0] if rows else {}


def fetch_totals(team_id: int, roster_window: dict[str, Any], stat_type: str) -> dict[str, Any]:
    params = {
        "Season": SEASON,
        "SeasonType": SEASON_TYPE,
        "TeamId": str(team_id),
        "Type": stat_type,
        "FromDate": roster_window["from_date"],
        "ToDate": roster_window["to_date"],
    }
    row = totals_row(get(TOTALS_URL, params), team_id)
    if not row:
        raise RuntimeError(f"no {stat_type} totals for {team_id}")
    return row


def fetch_wowy_off(team_id: int, roster_window: dict[str, Any], stat_type: str) -> dict[str, Any]:
    params = {
        "Season": SEASON,
        "SeasonType": SEASON_TYPE,
        "TeamId": str(team_id),
        "Type": stat_type,
        "FromDate": roster_window["from_date"],
        "ToDate": roster_window["to_date"],
        "0Exactly1OffFloor": PLAYER_ID,
    }
    row = get(WOWY_URL, params).get("single_row_table_data", {})
    return row if isinstance(row, dict) else {}


def num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(row.get(key))
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass
    return None


def metrics(team: dict[str, Any], opponent: dict[str, Any]) -> dict[str, Any]:
    team_points, opponent_points = num(team, "Points", "Pts"), num(opponent, "Points", "Pts")
    team_possessions, opponent_possessions = num(team, "Possessions"), num(
        opponent, "Possessions"
    )
    team_oreb, team_dreb = num(team, "OffRebounds", "OREB"), num(
        team, "DefRebounds", "DREB"
    )
    opponent_oreb, opponent_dreb = num(opponent, "OffRebounds", "OREB"), num(
        opponent, "DefRebounds", "DREB"
    )

    def pct(numerator: float | None, denominator: float | None) -> float | None:
        return None if numerator is None or not denominator else 100 * numerator / denominator

    team_rebounds = (
        None if team_oreb is None or team_dreb is None else team_oreb + team_dreb
    )
    opponent_rebounds = (
        None
        if opponent_oreb is None or opponent_dreb is None
        else opponent_oreb + opponent_dreb
    )
    seconds = num(team, "SecondsPlayed") or num(opponent, "SecondsPlayed")
    return {
        "minutes": num(team, "Minutes")
        or num(opponent, "Minutes")
        or (seconds / 60 if seconds is not None else None),
        "offensive_rating": pct(team_points, team_possessions),
        "defensive_rating": pct(opponent_points, opponent_possessions),
        "net_rating": None
        if None in (team_points, opponent_points, team_possessions, opponent_possessions)
        or not team_possessions
        or not opponent_possessions
        else 100 * team_points / team_possessions
        - 100 * opponent_points / opponent_possessions,
        "off_rebound_pct": pct(
            team_oreb,
            None if team_oreb is None or opponent_dreb is None else team_oreb + opponent_dreb,
        ),
        "def_rebound_pct": pct(
            team_dreb,
            None if team_dreb is None or opponent_oreb is None else team_dreb + opponent_oreb,
        ),
        "total_rebound_pct": pct(
            team_rebounds,
            None
            if team_rebounds is None or opponent_rebounds is None
            else team_rebounds + opponent_rebounds,
        ),
    }


def count_fields(row: dict[str, Any]) -> dict[str, float]:
    keys = [
        "Minutes",
        "Points",
        "Possessions",
        "OffRebounds",
        "DefRebounds",
        "FG2M",
        "FG2A",
        "FG3M",
        "FG3A",
        "Turnovers",
    ]
    return {key: value for key in keys if (value := num(row, key)) is not None}


def identity(left_row: dict[str, Any], right_row: dict[str, Any]) -> dict[str, Any]:
    left, right = count_fields(left_row), count_fields(right_row)
    common = sorted(set(left) & set(right))
    differences = {
        key: right[key] - left[key]
        for key in common
        if abs(right[key] - left[key]) > 1e-7
    }
    return {"match": not differences, "common_fields": common, "differences": differences}


def main() -> int:
    trade, movement = official_trade_date()
    if trade is None:
        raise RuntimeError("could not find Adams' Memphis-to-Houston trade date")

    roster_html = get(REALGM_ROSTER, text=True)
    if PLAYER_NAME.casefold() not in roster_html.casefold():
        raise RuntimeError("Adams was not found on the 2023-24 Memphis roster page")

    windows = build_windows(fetch_games(), trade)
    segments = []
    for name, team_id in (("memphis", MEM), ("houston", HOU)):
        roster_window = windows[name]
        team = fetch_totals(team_id, roster_window, "Team")
        opponent = fetch_totals(team_id, roster_window, "Opponent")
        segment = {
            "team": name,
            "team_id": team_id,
            "window": roster_window,
            "off_metrics": metrics(team, opponent),
        }
        try:
            wowy_team = fetch_wowy_off(team_id, roster_window, "Team")
            wowy_opponent = fetch_wowy_off(team_id, roster_window, "Opponent")
            segment["wowy_zero_minute_identity"] = {
                "team": identity(team, wowy_team),
                "opponent": identity(opponent, wowy_opponent),
                "metrics": metrics(wowy_team, wowy_opponent),
            }
        except Exception as exc:
            segment["wowy_zero_minute_identity"] = {
                "error": repr(exc),
                "fallback": (
                    "For a zero-minute roster tenure, all team possessions are OFF by definition."
                ),
            }
        segments.append(segment)

    report = {
        "definition": (
            "Roster-window OFF includes every team possession while the player officially "
            "belonged to that team, including injury and DNP games."
        ),
        "season": SEASON,
        "player_id": PLAYER_ID,
        "player": PLAYER_NAME,
        "movement_feed": movement,
        "roster_validation": {"source": REALGM_ROSTER, "contains_player": True},
        "roster_windows": windows,
        "segments": segments,
        "preflight_passed": all(
            segment["off_metrics"]["minutes"] is not None for segment in segments
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "preflight_passed": report["preflight_passed"],
                "movement_minimum_date": movement.get("minimum_date"),
                "trade_date": windows["trade_date"],
                "same_day_resolution": windows["same_day_resolution"],
                "memphis_window": windows["memphis"],
                "houston_window": windows["houston"],
                "segments": [
                    {
                        "team": segment["team"],
                        **segment["off_metrics"],
                        "wowy_match": segment.get("wowy_zero_minute_identity", {})
                        .get("team", {})
                        .get("match"),
                    }
                    for segment in segments
                ],
                "report": str(OUT),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
