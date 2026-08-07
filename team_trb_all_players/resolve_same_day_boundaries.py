from __future__ import annotations

import argparse
import gzip
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
ROSTER = BASE / "impact_database" / "roster_tenure"
INPUT = ROSTER / "player_team_season_windows_schedule_audited.jsonl.gz"
OUTPUT = ROSTER / "player_team_season_windows_evidence_audited.jsonl.gz"
AUDIT = ROSTER / "same_day_evidence_audit.json"
SUMMARY = ROSTER / "same_day_evidence_summary.json"
CACHE = ROSTER / "player_game_log_cache"

NBA_URL = "https://stats.nba.com/stats/playergamelog"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
}


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def clean_team_id(value: Any) -> int | None:
    try:
        n = int(str(value).strip())
    except Exception:
        return None
    return n if n > 0 else None


def normalize_game_log(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(sets, dict):
        sets = [sets]
    if not isinstance(sets, list):
        return []
    target = None
    for item in sets:
        if isinstance(item, dict) and item.get("headers") and item.get("rowSet") is not None:
            target = item
            if str(item.get("name", "")).casefold() in {"playergamelog", "playergamelogs"}:
                break
    if not target:
        return []
    headers = [str(x) for x in target["headers"]]
    out = []
    for raw in target.get("rowSet", []):
        row = dict(zip(headers, raw))
        game_id = str(row.get("Game_ID") or row.get("GAME_ID") or "").strip()
        team_id = clean_team_id(row.get("Team_ID") or row.get("TEAM_ID"))
        if game_id and team_id:
            out.append({
                "game_id": game_id,
                "team_id": team_id,
                "game_date": str(row.get("GAME_DATE") or row.get("Game_Date") or "").strip(),
                "minutes": row.get("MIN"),
            })
    return out


def fetch_player_games(player_id: str, season: str, attempts: int = 3) -> tuple[list[dict[str, Any]], str | None]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / f"{season}_{player_id}.json.gz"
    if cache_path.exists():
        try:
            with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            return normalize_game_log(payload), None
        except Exception:
            pass

    params = {
        "PlayerID": player_id,
        "Season": season,
        "SeasonType": "Regular Season",
    }
    errors = []
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(NBA_URL, params=params, headers=HEADERS, timeout=(8, 25))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
            games = normalize_game_log(payload)
            with gzip.open(cache_path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            time.sleep(0.55)
            return games, None
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    return [], " | ".join(errors)


def positive_participation_evidence(row: dict[str, Any], games: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    team_id = int(row["team_id"])
    boundary_ids = {str(x) for x in row.get("boundary_game_ids") or []}
    evidence = {}
    for game in games:
        if str(game["game_id"]) in boundary_ids and int(game["team_id"]) == team_id:
            evidence[str(game["game_id"])] = game
    return evidence


def apply_evidence(row: dict[str, Any], games: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(row)
    boundary_ids = [str(x) for x in row.get("boundary_game_ids") or []]
    if row.get("schedule_boundary_status") != "needs_ordering_evidence" or not boundary_ids:
        return out

    evidence = positive_participation_evidence(row, games)
    resolved_ids = sorted(evidence)
    unresolved_ids = sorted(set(boundary_ids) - set(resolved_ids))
    out["same_day_positive_participation_game_ids"] = resolved_ids
    out["same_day_unresolved_game_ids"] = unresolved_ids
    out["same_day_participation_evidence"] = [evidence[k] for k in resolved_ids]

    # Positive participation proves the player was on this team's active roster for
    # that boundary game. Absence from a player game log proves nothing about roster
    # tenure (injury/DNP/suspension are valid OFF), so negative evidence is never used.
    if not unresolved_ids:
        out["team_games_in_window"] = int(out["team_games_min"]) + len(resolved_ids)
        out["team_games_min"] = out["team_games_in_window"]
        out["team_games_max"] = out["team_games_in_window"]
        out["same_day_resolution"] = "resolved_by_positive_player_game_participation"
        out["schedule_boundary_status"] = "resolved"
        flags = [f for f in (out.get("audit_flags") or []) if f != "same_day_game_ordering_evidence_required"]
        out["audit_flags"] = sorted(set(flags))
    elif resolved_ids:
        out["team_games_min"] = int(out["team_games_min"]) + len(resolved_ids)
        out["same_day_resolution"] = "partially_resolved_by_positive_player_game_participation; remaining boundary requires ordering evidence"
    else:
        out["same_day_resolution"] = "unresolved; no positive participation evidence and non-participation is not roster evidence"
    return out


def build() -> dict[str, Any]:
    rows = read_jsonl_gz(INPUT)
    targets = sorted({
        (str(r["player_id"]), str(r["season"]))
        for r in rows
        if r.get("schedule_boundary_status") == "needs_ordering_evidence"
        and str(r.get("player_id") or "").isdigit()
    })

    logs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    fetch_errors: list[dict[str, str]] = []
    for i, (player_id, season) in enumerate(targets, 1):
        games, error = fetch_player_games(player_id, season)
        logs[(player_id, season)] = games
        if error:
            fetch_errors.append({"player_id": player_id, "season": season, "error": error})
        if i % 25 == 0 or i == len(targets):
            print(f"player-game evidence {i}/{len(targets)}; fetch_errors={len(fetch_errors)}")

    output_rows = [
        apply_evidence(r, logs.get((str(r.get("player_id")), str(r.get("season"))), []))
        for r in rows
    ]
    with gzip.open(OUTPUT, "wt", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    before = sum(r.get("schedule_boundary_status") == "needs_ordering_evidence" for r in rows)
    after = sum(r.get("schedule_boundary_status") == "needs_ordering_evidence" for r in output_rows)
    fully_resolved = before - after
    partially_resolved = sum(
        r.get("schedule_boundary_status") == "needs_ordering_evidence"
        and bool(r.get("same_day_positive_participation_game_ids"))
        for r in output_rows
    )
    unresolved = [r for r in output_rows if r.get("schedule_boundary_status") == "needs_ordering_evidence"]

    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method": "positive player-game participation only; non-participation never interpreted as off-roster",
        "input_unresolved_windows": before,
        "fully_resolved_windows": fully_resolved,
        "partially_resolved_windows": partially_resolved,
        "remaining_unresolved_windows": after,
        "player_seasons_queried": len(targets),
        "fetch_error_count": len(fetch_errors),
        "fetch_errors": fetch_errors,
        "remaining_unresolved": unresolved,
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {k: v for k, v in audit.items() if k not in {"fetch_errors", "remaining_unresolved"}}
    summary["output"] = str(OUTPUT)
    summary["audit"] = str(AUDIT)
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    payload = {
        "resultSets": [{
            "name": "PlayerGameLog",
            "headers": ["GAME_ID", "TEAM_ID", "GAME_DATE", "MIN"],
            "rowSet": [["0022300002", 10, "FEB 01, 2024", 12]],
        }]
    }
    games = normalize_game_log(payload)
    assert games == [{"game_id": "0022300002", "team_id": 10, "game_date": "FEB 01, 2024", "minutes": 12}]
    row = {
        "season": "2023-24", "player_id": "123", "team_id": 10,
        "schedule_boundary_status": "needs_ordering_evidence",
        "boundary_game_ids": ["0022300002"], "team_games_min": 4, "team_games_max": 5,
        "audit_flags": ["same_day_game_ordering_evidence_required"],
    }
    out = apply_evidence(row, games)
    assert out["schedule_boundary_status"] == "resolved"
    assert out["team_games_in_window"] == 5

    no_game = apply_evidence(row, [])
    assert no_game["schedule_boundary_status"] == "needs_ordering_evidence"
    assert no_game["team_games_min"] == 4
    print("resolve_same_day_boundaries self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
