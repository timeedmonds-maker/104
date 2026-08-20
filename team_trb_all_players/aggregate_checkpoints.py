from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

START_YEAR = 2000
END_YEAR = 2025
MIN_MINUTES = 10_000.0
ROOT = Path(os.environ.get("PBPSTATS_CHECKPOINT_ROOT", "team_trb_all_players/checkpoints"))
OUT = Path(
    os.environ.get(
        "PBPSTATS_CONSOLIDATED_DIR",
        "team_trb_all_players/consolidated_output",
    )
)
OUT.mkdir(parents=True, exist_ok=True)

DETAIL_FIELDS = [
    "player_id",
    "player",
    "season",
    "team_id",
    "team",
    "seconds",
    "minutes",
    "team_rebounds",
    "opponent_rebounds",
    "rebound_events",
    "team_trb_pct",
    "lineups",
]


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def as_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def main() -> None:
    expected_seasons = [season_label(year) for year in range(START_YEAR, END_YEAR + 1)]
    complete_markers = sorted(ROOT.glob("**/season_complete.json"))
    complete_dirs: dict[str, Path] = {}
    duplicate_seasons: list[str] = []

    for marker_path in complete_markers:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        season = str(marker["season"])
        if season in complete_dirs:
            duplicate_seasons.append(season)
        else:
            complete_dirs[season] = marker_path.parent

    detail_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    seen_detail_keys: set[tuple[str, str, str]] = set()
    duplicate_detail_keys: list[tuple[str, str, str]] = []

    for season in sorted(complete_dirs):
        checkpoint_dir = complete_dirs[season]
        detail_path = checkpoint_dir / "player_team_season_detail.csv"
        audit_path = checkpoint_dir / "team_season_request_audit.csv"
        if not detail_path.exists() or not audit_path.exists():
            continue

        for row in read_csv(detail_path):
            key = (row["player_id"], row["season"], row["team_id"])
            if key in seen_detail_keys:
                duplicate_detail_keys.append(key)
                continue
            seen_detail_keys.add(key)
            detail_rows.append(
                {
                    "player_id": row["player_id"],
                    "player": row["player"],
                    "season": row["season"],
                    "team_id": row["team_id"],
                    "team": row["team"],
                    "seconds": as_float(row["seconds"]),
                    "minutes": as_float(row["minutes"]),
                    "team_rebounds": as_float(row["team_rebounds"]),
                    "opponent_rebounds": as_float(row["opponent_rebounds"]),
                    "rebound_events": as_float(row["rebound_events"]),
                    "team_trb_pct": as_float(row["team_trb_pct"]),
                    "lineups": int(float(row["lineups"])),
                }
            )

        audit_rows.extend(read_csv(audit_path))

    detail_rows.sort(key=lambda row: (row["season"], row["team_id"], row["player_id"]))
    formatted_detail = [
        {
            **row,
            "seconds": round(row["seconds"], 1),
            "minutes": round(row["minutes"], 3),
            "team_rebounds": round(row["team_rebounds"], 3),
            "opponent_rebounds": round(row["opponent_rebounds"], 3),
            "rebound_events": round(row["rebound_events"], 3),
            "team_trb_pct": round(row["team_trb_pct"], 6),
        }
        for row in detail_rows
    ]
    write_csv(OUT / "player_team_season_detail.csv", formatted_detail, DETAIL_FIELDS)

    audit_fields = [
        "season",
        "team_id",
        "team",
        "lineup_rows",
        "opponent_rows",
        "team_minutes",
        "status",
    ]
    audit_rows.sort(key=lambda row: (row["season"], int(row["team_id"])))
    write_csv(OUT / "team_season_request_audit.csv", audit_rows, audit_fields)

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

    leaderboard_fields = [
        "rank",
        "player",
        "player_id",
        "minutes",
        "career_team_trb_pct",
        "team_rebounds",
        "opponent_rebounds",
        "rebound_events",
        "seasons_included",
        "first_season",
        "last_season",
        "team_season_stints",
    ]
    write_csv(OUT / "career_team_trb_leaderboard.csv", leaderboard, leaderboard_fields)
    (OUT / "top_50.json").write_text(
        json.dumps(leaderboard[:50], indent=2), encoding="utf-8"
    )

    complete_seasons = sorted(complete_dirs)
    missing_seasons = [season for season in expected_seasons if season not in complete_dirs]
    active_team_seasons = sum(1 for row in audit_rows if row.get("status") == "active")
    expected_active_team_seasons = 4 * 29 + 22 * 30

    validation_errors: list[str] = []
    if duplicate_seasons:
        validation_errors.append(f"duplicate season artifacts: {sorted(set(duplicate_seasons))}")
    if duplicate_detail_keys:
        validation_errors.append(f"duplicate player-team-season rows: {len(duplicate_detail_keys)}")
    if missing_seasons:
        validation_errors.append(f"missing seasons: {missing_seasons}")
    if not missing_seasons and active_team_seasons != expected_active_team_seasons:
        validation_errors.append(
            f"active team-seasons={active_team_seasons}, expected={expected_active_team_seasons}"
        )
    if not missing_seasons and len(detail_rows) < 8_000:
        validation_errors.append(f"too few player-team-season rows: {len(detail_rows)}")
    if not missing_seasons and len(leaderboard) < 150:
        validation_errors.append(f"too few qualifying players: {len(leaderboard)}")

    metadata = {
        "expected_seasons": expected_seasons,
        "complete_seasons": complete_seasons,
        "missing_seasons": missing_seasons,
        "complete_season_count": len(complete_seasons),
        "active_team_seasons": active_team_seasons,
        "expected_active_team_seasons": expected_active_team_seasons,
        "player_team_season_rows": len(detail_rows),
        "unique_players": len(career),
        "qualifying_players": len(leaderboard),
        "minimum_minutes": MIN_MINUTES,
        "complete": not validation_errors,
        "validation_errors": validation_errors,
        "method": (
            "Combine validated season checkpoints, then aggregate raw lineup-assigned "
            "team and opponent rebound counts across all player team-seasons."
        ),
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2), flush=True)
    print(json.dumps(leaderboard[:20], indent=2), flush=True)

    if validation_errors:
        raise SystemExit("; ".join(validation_errors))


if __name__ == "__main__":
    main()
