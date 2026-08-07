from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROSTER = BASE / "impact_database" / "roster_tenure"
WINDOWS = ROSTER / "player_team_season_windows.jsonl.gz"
GAMES = ROSTER / "regular_season_games.jsonl.gz"
FINAL = ROSTER / "player_team_season_windows_schedule_audited.jsonl.gz"
AUDIT = ROSTER / "schedule_boundary_audit.json"
SUMMARY = ROSTER / "schedule_boundary_summary.json"


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def next_day(day: str) -> str:
    return (datetime.fromisoformat(day).date() + timedelta(days=1)).isoformat()


def previous_day(day: str) -> str:
    return (datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()


def team_game_index(games: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        season = str(game["season"])
        for field in ("home_team_id", "away_team_id"):
            index[(season, int(game[field]))].append(game)
    for key in index:
        index[key].sort(key=lambda g: (g["game_date"], g["game_id"]))
    return dict(index)


def games_between(team_games: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    return [g for g in team_games if start <= g["game_date"] <= end]


def boundary_game(team_games: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [g for g in team_games if g["game_date"] == day]


def audit_window(row: dict[str, Any], team_games: list[dict[str, Any]]) -> dict[str, Any]:
    output = dict(row)
    start = str(row["tenure_start"])
    end = str(row["tenure_end"])
    start_is_txn = str(row.get("start_reason", "")).endswith("_into_team")
    end_is_txn = str(row.get("end_reason", "")).endswith("_out_of_team")
    start_games = boundary_game(team_games, start) if start_is_txn else []
    end_games = boundary_game(team_games, end) if end_is_txn else []

    inclusive_games = games_between(team_games, start, end)
    ambiguous_ids = sorted({g["game_id"] for g in start_games + end_games})
    ambiguity_count = len(ambiguous_ids)

    # We deliberately do not guess ordering inside a transaction date. If a team
    # did not play that date, the date boundary is fully resolved for game-count
    # purposes. If it did play, later evidence (transaction timestamp or player
    # game participation) must decide inclusion.
    if ambiguity_count == 0:
        output["team_games_in_window"] = len(inclusive_games)
        output["team_games_min"] = len(inclusive_games)
        output["team_games_max"] = len(inclusive_games)
        output["same_day_resolution"] = "resolved_no_team_game_on_transaction_boundary"
        output["schedule_boundary_status"] = "resolved"
    else:
        output["team_games_in_window"] = None
        output["team_games_min"] = len(inclusive_games) - ambiguity_count
        output["team_games_max"] = len(inclusive_games)
        output["same_day_resolution"] = "unresolved_team_game_on_transaction_date; requires ordering evidence"
        output["schedule_boundary_status"] = "needs_ordering_evidence"

    output["boundary_game_ids"] = ambiguous_ids
    flags = list(output.get("audit_flags") or [])
    flags = [flag for flag in flags if flag != "same_day_game_check_required"]
    if ambiguity_count:
        flags.append("same_day_game_ordering_evidence_required")
    output["audit_flags"] = sorted(set(flags))
    return output


def build() -> dict[str, Any]:
    windows = read_jsonl_gz(WINDOWS)
    games = read_jsonl_gz(GAMES)
    index = team_game_index(games)

    final_rows: list[dict[str, Any]] = []
    missing_schedule: list[dict[str, Any]] = []
    for row in windows:
        key = (str(row["season"]), int(row["team_id"]))
        team_games = index.get(key, [])
        if not team_games:
            missing_schedule.append({
                "season": row["season"],
                "team_id": row["team_id"],
                "team_abbr": row.get("team_abbr"),
            })
            out = dict(row)
            out["team_games_in_window"] = None
            out["team_games_min"] = None
            out["team_games_max"] = None
            out["schedule_boundary_status"] = "missing_team_schedule"
            out["boundary_game_ids"] = []
            final_rows.append(out)
            continue
        final_rows.append(audit_window(row, team_games))

    missing_unique = sorted(
        {json.dumps(item, sort_keys=True) for item in missing_schedule}
    )
    missing_unique = [json.loads(item) for item in missing_unique]
    unresolved = [row for row in final_rows if row.get("schedule_boundary_status") == "needs_ordering_evidence"]
    resolved = [row for row in final_rows if row.get("schedule_boundary_status") == "resolved"]

    with gzip.open(FINAL, "wt", encoding="utf-8") as handle:
        for row in final_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "unresolved_boundary_window_count": len(unresolved),
        "unresolved_boundary_windows": unresolved,
        "missing_team_schedule_count": len(missing_unique),
        "missing_team_schedules": missing_unique,
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_count": len(final_rows),
        "exact_team_game_count_windows": sum(row.get("team_games_in_window") is not None for row in final_rows),
        "schedule_boundary_resolved_windows": len(resolved),
        "same_day_ordering_evidence_required": len(unresolved),
        "missing_team_schedules": len(missing_unique),
        "output": str(FINAL),
        "audit": str(AUDIT),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    games = [
        {"season": "2023-24", "game_id": "1", "game_date": "2024-01-31", "home_team_id": 10, "away_team_id": 20},
        {"season": "2023-24", "game_id": "2", "game_date": "2024-02-01", "home_team_id": 10, "away_team_id": 30},
        {"season": "2023-24", "game_id": "3", "game_date": "2024-02-03", "home_team_id": 10, "away_team_id": 40},
    ]
    index = team_game_index(games)
    row = {
        "season": "2023-24", "team_id": 10, "tenure_start": "2023-10-24", "tenure_end": "2024-02-01",
        "start_reason": "season_open_roster_continuity", "end_reason": "trade_out_of_team",
        "audit_flags": ["same_day_game_check_required"],
    }
    out = audit_window(row, index[("2023-24", 10)])
    assert out["team_games_in_window"] is None
    assert out["team_games_min"] == 1 and out["team_games_max"] == 2
    assert out["boundary_game_ids"] == ["2"]

    no_boundary = dict(row, tenure_end="2024-02-02")
    out2 = audit_window(no_boundary, index[("2023-24", 10)])
    assert out2["team_games_in_window"] == 2
    assert out2["schedule_boundary_status"] == "resolved"
    print("finalize_roster_tenure_windows self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
