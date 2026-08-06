from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
REPORT = BASE / "impact_database" / "historical_roster_boxscore_preflight.json"
PLAYER_ID = "87"
PLAYER_NAME = "Dikembe Mutombo"
TEAM_ID = 1610612737


def log(message: str) -> None:
    print(message, flush=True)


def roster_players(payload: dict[str, Any], team_id: int) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    for key in ("home", "visitor"):
        team = data.get(key, {}) if isinstance(data, dict) else {}
        if str(team.get("teamId") or team.get("tid") or "") == str(team_id):
            players = team.get("players") or team.get("pl") or []
            return players if isinstance(players, list) else []
    return []


def player_matches(row: dict[str, Any]) -> bool:
    pid = str(row.get("personId") or row.get("pid") or row.get("PLAYER_ID") or "")
    name = str(
        row.get("name")
        or row.get("playerName")
        or f"{row.get('firstName', row.get('fn', ''))} {row.get('lastName', row.get('ln', ''))}"
    ).strip()
    return pid == PLAYER_ID or name.casefold() == PLAYER_NAME.casefold()


def main() -> int:
    if not REPORT.exists():
        raise RuntimeError(f"missing prior report: {REPORT}")
    prior = json.loads(REPORT.read_text(encoding="utf-8"))
    check = prior["results"]["atl_before"]
    game_id = str(check["game_id"])
    log(f"Testing official NBA Mobile Stats roster feed for game {game_id}")

    candidates = []
    for season_year in ("2000", "2001"):
        candidates.extend(
            [
                f"https://api.nba.com/v0/api/mobilefeed/nba/{season_year}/scores/roster_lineup/{game_id}_roster_lineup.json",
                f"https://data.nba.com/data/v2022/json/mobile_teams/nba/{season_year}/scores/roster_lineup/{game_id}_roster_lineup.json",
                f"https://data.nba.com/data/10s/v2015/json/mobile_teams/nba/{season_year}/scores/roster_lineup/{game_id}_roster_lineup.json",
            ]
        )

    attempts = []
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://www.nba.com/",
    }

    for url in candidates:
        try:
            log(f"GET {url}")
            response = requests.get(url, headers=headers, timeout=(8, 12))
            record = {
                "url": url,
                "status": response.status_code,
                "bytes": len(response.content),
            }
            if response.status_code == 200:
                payload = response.json()
                players = roster_players(payload, TEAM_ID)
                record.update(
                    {
                        "team_players": len(players),
                        "mutombo_found": any(player_matches(row) for row in players),
                    }
                )
                attempts.append(record)
                print(json.dumps(record, indent=2), flush=True)
                if players:
                    passed = bool(record["mutombo_found"])
                    summary = {
                        "preflight_passed": passed,
                        "game_id": game_id,
                        "working_url": url,
                        "team_players": len(players),
                        "mutombo_found": record["mutombo_found"],
                        "attempts": attempts,
                    }
                    print(json.dumps(summary, indent=2), flush=True)
                    return 0 if passed else 1
            attempts.append(record)
            print(json.dumps(record), flush=True)
        except Exception as exc:
            record = {"url": url, "error": repr(exc)}
            attempts.append(record)
            print(json.dumps(record), flush=True)

    print(
        json.dumps(
            {
                "preflight_passed": False,
                "game_id": game_id,
                "reason": "No NBA Mobile Stats roster endpoint returned a usable roster.",
                "attempts": attempts,
            },
            indent=2,
        ),
        flush=True,
    )
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
