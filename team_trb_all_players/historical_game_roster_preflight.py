from __future__ import annotations

import json
import time
from datetime import date, datetime

import requests

SEASON = "2000-01"
SEASON_TYPE = "Regular Season"
PLAYER_ID = "87"  # Dikembe Mutombo
PLAYER_NAME = "Dikembe Mutombo"
ATL = 1610612737
PHI = 1610612755
TRADE_DATE = date(2001, 2, 22)

GAMES_URL = "https://api.pbpstats.com/get-games/nba"
BOX_SUMMARY_URL = "https://stats.nba.com/stats/boxscoresummaryv2"
BOX_TRADITIONAL_URL = "https://stats.nba.com/stats/boxscoretraditionalv2"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
}


def get_json(url: str, params: dict[str, str]) -> dict:
    print(f"GET {url} {params}", flush=True)
    last = None
    for attempt in range(2):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=(10, 25))
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            print(f"  attempt {attempt + 1} failed: {exc!r}", flush=True)
            time.sleep(2)
    raise RuntimeError(f"request failed: {url}: {last!r}")


def rows(payload: dict, name: str) -> list[dict]:
    result_sets = payload.get("resultSets", [])
    if isinstance(result_sets, dict):
        result_sets = [result_sets]
    for result_set in result_sets:
        if result_set.get("name") == name:
            headers = result_set.get("headers", [])
            return [dict(zip(headers, row)) for row in result_set.get("rowSet", [])]
    return []


def roster_evidence(game_id: str, team_id: int) -> dict:
    evidence = []
    summary = get_json(BOX_SUMMARY_URL, {"GameID": game_id})
    for row in rows(summary, "InactivePlayers"):
        if str(row.get("TEAM_ID") or "") != str(team_id):
            continue
        pid = str(row.get("PLAYER_ID") or "")
        name = str(row.get("PLAYER_NAME") or "").strip()
        if pid == PLAYER_ID or name.casefold() == PLAYER_NAME.casefold():
            evidence.append({"source": "InactivePlayers", "row": row})

    traditional = get_json(
        BOX_TRADITIONAL_URL,
        {
            "GameID": game_id,
            "StartPeriod": "1",
            "EndPeriod": "10",
            "StartRange": "0",
            "EndRange": "0",
            "RangeType": "0",
        },
    )
    for row in rows(traditional, "PlayerStats"):
        if str(row.get("TEAM_ID") or "") != str(team_id):
            continue
        pid = str(row.get("PLAYER_ID") or "")
        name = str(row.get("PLAYER_NAME") or "").strip()
        if pid == PLAYER_ID or name.casefold() == PLAYER_NAME.casefold():
            evidence.append({"source": "PlayerStats", "row": row})

    return {"rostered": bool(evidence), "evidence": evidence}


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> int:
    games = get_json(GAMES_URL, {"Season": SEASON, "SeasonType": SEASON_TYPE}).get("results", [])
    nearby = []
    for game in games:
        game_date = parse_date(game["Date"])
        if abs((game_date - TRADE_DATE).days) > 5:
            continue
        teams = {int(game["HomeTeamId"]), int(game["AwayTeamId"])}
        if ATL in teams:
            nearby.append((game_date, game, ATL, "ATL"))
        if PHI in teams:
            nearby.append((game_date, game, PHI, "PHI"))

    nearby.sort(key=lambda item: (item[0], item[3], item[1]["GameId"]))
    output = []
    for game_date, game, team_id, team in nearby:
        check = roster_evidence(str(game["GameId"]), team_id)
        item = {
            "date": game_date.isoformat(),
            "game_id": str(game["GameId"]),
            "team": team,
            "rostered": check["rostered"],
            "sources": [entry["source"] for entry in check["evidence"]],
        }
        output.append(item)
        print(json.dumps(item), flush=True)

    atl_before = [x for x in output if x["team"] == "ATL" and x["date"] < TRADE_DATE.isoformat()]
    atl_after = [x for x in output if x["team"] == "ATL" and x["date"] > TRADE_DATE.isoformat()]
    phi_before = [x for x in output if x["team"] == "PHI" and x["date"] < TRADE_DATE.isoformat()]
    phi_after = [x for x in output if x["team"] == "PHI" and x["date"] > TRADE_DATE.isoformat()]

    passed = (
        bool(atl_before)
        and all(x["rostered"] for x in atl_before)
        and all(not x["rostered"] for x in atl_after)
        and all(not x["rostered"] for x in phi_before)
        and bool(phi_after)
        and all(x["rostered"] for x in phi_after)
    )
    summary = {
        "passed": passed,
        "season": SEASON,
        "trade_date": TRADE_DATE.isoformat(),
        "definition": "Official per-game roster = PlayerStats union InactivePlayers.",
        "checks": output,
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
