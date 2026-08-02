from __future__ import annotations

import csv
import json
import math
import os
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

START_YEAR = 2000
END_YEAR = 2025
MIN_MINUTES = 10_000.0
MAX_ROWS = 500
WORKERS = int(os.environ.get("PBPSTATS_WORKERS", "8"))

API_URL = "https://api.pbpstats.com/get-totals/nba"
OUT = Path("team_trb_all_players/output")
OUT.mkdir(parents=True, exist_ok=True)

# Every NBA franchise active at some point from 2000-01 onward. Franchise IDs
# persist through relocations/rebrands (e.g. SEA/OKC and VAN/MEM).
TEAM_IDS = [
    1610612737, 1610612738, 1610612739, 1610612740, 1610612741,
    1610612742, 1610612743, 1610612744, 1610612745, 1610612746,
    1610612747, 1610612748, 1610612749, 1610612750, 1610612751,
    1610612752, 1610612753, 1610612754, 1610612755, 1610612756,
    1610612757, 1610612758, 1610612759, 1610612760, 1610612761,
    1610612762, 1610612763, 1610612764, 1610612765, 1610612766,
]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    ),
}

_thread_local = threading.local()


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Non-finite numeric value: {value!r}")
    return result


def fetch_rows(season: str, team_id: int, entity_type: str) -> list[dict[str, Any]]:
    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "Type": entity_type,
        "TeamId": str(team_id),
    }
    errors: list[str] = []
    for attempt in range(6):
        try:
            response = get_session().get(API_URL, params=params, timeout=150)
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("multi_row_table_data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError(
                    f"No multi_row_table_data list; payload keys="
                    f"{sorted(payload) if isinstance(payload, dict) else type(payload).__name__}"
                )
            return rows
        except Exception as exc:
            errors.append(f"attempt {attempt + 1}: {exc!r}")
            if attempt < 5:
                time.sleep(min(2 ** attempt, 20) + random.random())
    raise RuntimeError("; ".join(errors))


@dataclass
class TeamSeasonResult:
    season: str
    team_id: int
    team_abbreviation: str
    lineup_rows: int
    opponent_rows: int
    seconds: float
    player_rows: list[dict[str, Any]]


def process_team_season(season: str, team_id: int) -> TeamSeasonResult:
    lineups = fetch_rows(season, team_id, "Lineup")
    opponents = fetch_rows(season, team_id, "LineupOpponent")

    # An inactive franchise correctly returns two empty lists.
    if not lineups and not opponents:
        return TeamSeasonResult(season, team_id, "", 0, 0, 0.0, [])
    if not lineups or not opponents:
        raise ValueError(
            f"One-sided response for {season} {team_id}: "
            f"lineups={len(lineups)}, opponents={len(opponents)}"
        )
    if len(lineups) >= MAX_ROWS or len(opponents) >= MAX_ROWS:
        raise ValueError(
            f"Possible 500-row truncation for {season} {team_id}: "
            f"lineups={len(lineups)}, opponents={len(opponents)}"
        )

    lineup_by_id = {str(row.get("EntityId")): row for row in lineups}
    opponent_by_id = {str(row.get("EntityId")): row for row in opponents}
    if len(lineup_by_id) != len(lineups):
        raise ValueError(f"Duplicate lineup EntityId for {season} {team_id}")
    if len(opponent_by_id) != len(opponents):
        raise ValueError(f"Duplicate opponent EntityId for {season} {team_id}")
    if lineup_by_id.keys() != opponent_by_id.keys():
        only_team = sorted(lineup_by_id.keys() - opponent_by_id.keys())[:10]
        only_opp = sorted(opponent_by_id.keys() - lineup_by_id.keys())[:10]
        raise ValueError(
            f"Lineup/opponent ID mismatch for {season} {team_id}; "
            f"team-only={only_team}, opponent-only={only_opp}"
        )

    team_abbreviation = str(lineups[0].get("TeamAbbreviation") or "")
    player_acc: dict[str, dict[str, Any]] = {}
    total_seconds = 0.0

    for lineup_id, row in lineup_by_id.items():
        opp = opponent_by_id[lineup_id]
        player_ids = lineup_id.split("-")
        player_names = [x.strip() for x in str(row.get("Name") or "").split(",")]
        if len(player_ids) != 5:
            raise ValueError(f"Not a five-player lineup: {lineup_id!r}")
        if len(player_names) != 5:
            raise ValueError(
                f"Could not parse five names for {season} {team_id} {lineup_id}: "
                f"{row.get('Name')!r}"
            )

        seconds = as_float(row.get("SecondsPlayed"), as_float(row.get("Minutes")) * 60.0)
        opponent_seconds = as_float(opp.get("SecondsPlayed"), seconds)
        if seconds < 0:
            raise ValueError(f"Negative seconds for {season} {team_id} {lineup_id}")
        if abs(seconds - opponent_seconds) > 1.0:
            raise ValueError(
                f"Seconds mismatch for {season} {team_id} {lineup_id}: "
                f"team={seconds}, opponent={opponent_seconds}"
            )

        team_rebounds = as_float(row.get("Rebounds"))
        opponent_rebounds = as_float(opp.get("Rebounds"))
        total_rebound_events = team_rebounds + opponent_rebounds
        if team_rebounds < 0 or opponent_rebounds < 0:
            raise ValueError(f"Negative rebound total for {season} {team_id} {lineup_id}")

        total_seconds += seconds
        for player_id, player_name in zip(player_ids, player_names, strict=True):
            item = player_acc.setdefault(
                player_id,
                {
                    "player_id": player_id,
                    "name_seconds": Counter(),
                    "seconds": 0.0,
                    "team_rebounds": 0.0,
                    "opponent_rebounds": 0.0,
                    "rebound_events": 0.0,
                    "lineups": 0,
                },
            )
            item["name_seconds"][player_name] += seconds
            item["seconds"] += seconds
            item["team_rebounds"] += team_rebounds
            item["opponent_rebounds"] += opponent_rebounds
            item["rebound_events"] += total_rebound_events
            item["lineups"] += 1

    player_rows: list[dict[str, Any]] = []
    for player_id, item in player_acc.items():
        name = item["name_seconds"].most_common(1)[0][0]
        rebound_events = item["rebound_events"]
        player_rows.append(
            {
                "player_id": player_id,
                "player": name,
                "season": season,
                "team_id": team_id,
                "team": team_abbreviation,
                "seconds": item["seconds"],
                "minutes": item["seconds"] / 60.0,
                "team_rebounds": item["team_rebounds"],
                "opponent_rebounds": item["opponent_rebounds"],
                "rebound_events": rebound_events,
                "team_trb_pct": (
                    100.0 * item["team_rebounds"] / rebound_events
                    if rebound_events > 0
                    else None
                ),
                "lineups": item["lineups"],
            }
        )

    # Each lineup second belongs to five players exactly.
    player_seconds = sum(row["seconds"] for row in player_rows)
    if abs(player_seconds - 5.0 * total_seconds) > max(1.0, total_seconds * 1e-9):
        raise ValueError(
            f"Five-player seconds check failed for {season} {team_id}: "
            f"players={player_seconds}, lineups={total_seconds}"
        )

    return TeamSeasonResult(
        season=season,
        team_id=team_id,
        team_abbreviation=team_abbreviation,
        lineup_rows=len(lineups),
        opponent_rows=len(opponents),
        seconds=total_seconds,
        player_rows=player_rows,
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    seasons = [season_label(year) for year in range(START_YEAR, END_YEAR + 1)]
    tasks = [(season, team_id) for season in seasons for team_id in TEAM_IDS]
    results: list[TeamSeasonResult] = []
    failures: list[dict[str, Any]] = []

    print(
        f"Starting {len(tasks)} team-season builds across {len(seasons)} seasons "
        f"with {WORKERS} workers",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_map = {
            executor.submit(process_team_season, season, team_id): (season, team_id)
            for season, team_id in tasks
        }
        completed = 0
        for future in as_completed(future_map):
            season, team_id = future_map[future]
            completed += 1
            try:
                result = future.result()
                results.append(result)
                status = (
                    f"{result.team_abbreviation or 'inactive'} "
                    f"lineups={result.lineup_rows}"
                )
                print(f"[{completed}/{len(tasks)}] OK {season} {team_id} {status}", flush=True)
            except Exception as exc:
                failure = {"season": season, "team_id": team_id, "error": repr(exc)}
                failures.append(failure)
                print(f"[{completed}/{len(tasks)}] FAIL {failure}", flush=True)

    results.sort(key=lambda item: (item.season, item.team_id))
    active_results = [result for result in results if result.lineup_rows > 0]

    audit_rows = [
        {
            "season": result.season,
            "team_id": result.team_id,
            "team": result.team_abbreviation,
            "lineup_rows": result.lineup_rows,
            "opponent_rows": result.opponent_rows,
            "team_minutes": round(result.seconds / 60.0, 3),
            "status": "active" if result.lineup_rows else "inactive",
        }
        for result in results
    ]
    write_csv(
        OUT / "team_season_request_audit.csv",
        audit_rows,
        [
            "season", "team_id", "team", "lineup_rows", "opponent_rows",
            "team_minutes", "status",
        ],
    )

    if failures:
        write_csv(
            OUT / "request_failures.csv",
            failures,
            ["season", "team_id", "error"],
        )

    detail_rows = [row for result in active_results for row in result.player_rows]
    detail_rows.sort(key=lambda row: (row["season"], row["team_id"], row["player_id"]))
    formatted_detail = [
        {
            **row,
            "seconds": round(row["seconds"], 1),
            "minutes": round(row["minutes"], 3),
            "team_rebounds": round(row["team_rebounds"], 3),
            "opponent_rebounds": round(row["opponent_rebounds"], 3),
            "rebound_events": round(row["rebound_events"], 3),
            "team_trb_pct": (
                round(row["team_trb_pct"], 6)
                if row["team_trb_pct"] is not None
                else ""
            ),
        }
        for row in detail_rows
    ]
    write_csv(
        OUT / "player_team_season_detail.csv",
        formatted_detail,
        [
            "player_id", "player", "season", "team_id", "team", "seconds",
            "minutes", "team_rebounds", "opponent_rebounds", "rebound_events",
            "team_trb_pct", "lineups",
        ],
    )

    career: dict[str, dict[str, Any]] = {}
    for row in detail_rows:
        item = career.setdefault(
            row["player_id"],
            {
                "player_id": row["player_id"],
                "name_seconds": Counter(),
                "seconds": 0.0,
                "team_rebounds": 0.0,
                "opponent_rebounds": 0.0,
                "rebound_events": 0.0,
                "seasons": set(),
                "team_seasons": set(),
            },
        )
        item["name_seconds"][row["player"]] += row["seconds"]
        item["seconds"] += row["seconds"]
        item["team_rebounds"] += row["team_rebounds"]
        item["opponent_rebounds"] += row["opponent_rebounds"]
        item["rebound_events"] += row["rebound_events"]
        item["seasons"].add(row["season"])
        item["team_seasons"].add((row["season"], row["team_id"]))

    leaderboard: list[dict[str, Any]] = []
    for item in career.values():
        minutes = item["seconds"] / 60.0
        if minutes < MIN_MINUTES or item["rebound_events"] <= 0:
            continue
        player = item["name_seconds"].most_common(1)[0][0]
        leaderboard.append(
            {
                "rank": 0,
                "player": player,
                "player_id": item["player_id"],
                "minutes": round(minutes, 1),
                "career_team_trb_pct": round(
                    100.0 * item["team_rebounds"] / item["rebound_events"], 6
                ),
                "team_rebounds": round(item["team_rebounds"], 3),
                "opponent_rebounds": round(item["opponent_rebounds"], 3),
                "rebound_events": round(item["rebound_events"], 3),
                "seasons_included": len(item["seasons"]),
                "first_season": min(item["seasons"]),
                "last_season": max(item["seasons"]),
                "team_season_stints": len(item["team_seasons"]),
            }
        )

    leaderboard.sort(
        key=lambda row: (-row["career_team_trb_pct"], -row["minutes"], row["player"])
    )
    for rank, row in enumerate(leaderboard, start=1):
        row["rank"] = rank

    write_csv(
        OUT / "career_team_trb_leaderboard.csv",
        leaderboard,
        [
            "rank", "player", "player_id", "minutes", "career_team_trb_pct",
            "team_rebounds", "opponent_rebounds", "rebound_events",
            "seasons_included", "first_season", "last_season",
            "team_season_stints",
        ],
    )

    metadata = {
        "start_season": season_label(START_YEAR),
        "end_season": season_label(END_YEAR),
        "minimum_minutes": MIN_MINUTES,
        "team_season_tasks": len(tasks),
        "active_team_seasons": len(active_results),
        "inactive_team_seasons": len(results) - len(active_results),
        "failed_team_seasons": len(failures),
        "player_team_season_rows": len(detail_rows),
        "unique_players": len(career),
        "qualifying_players": len(leaderboard),
        "max_lineup_rows_for_any_team_season": max(
            (result.lineup_rows for result in active_results), default=0
        ),
        "method": (
            "For every team-season, pair PBP Stats Type=Lineup and "
            "Type=LineupOpponent by five-player EntityId. Allocate each lineup's "
            "seconds, team rebounds and opponent rebounds to all five players; then "
            "aggregate as sum(team rebounds) / sum(team rebounds + opponent rebounds)."
        ),
        "source": "https://api.pbpstats.com/get-totals/nba",
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (OUT / "top_50.json").write_text(
        json.dumps(leaderboard[:50], indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2), flush=True)
    print(json.dumps(leaderboard[:20], indent=2), flush=True)

    if failures:
        raise SystemExit(f"Build failed for {len(failures)} team-seasons")
    if len(active_results) < 740:
        raise SystemExit(f"Too few active team-seasons: {len(active_results)}")
    if len(detail_rows) < 8_000:
        raise SystemExit(f"Too few player-team-season rows: {len(detail_rows)}")
    if len(leaderboard) < 150:
        raise SystemExit(f"Too few qualifying players: {len(leaderboard)}")


if __name__ == "__main__":
    main()
