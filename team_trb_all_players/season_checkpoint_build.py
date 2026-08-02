from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import hf_schema_diagnostic as core

START_YEAR = int(os.environ["PBPSTATS_START_YEAR"])
WORKERS = int(os.environ.get("PBPSTATS_WORKERS", "2"))
OUT = Path(
    os.environ.get(
        "PBPSTATS_OUTPUT_DIR",
        f"team_trb_all_players/checkpoint_output/{START_YEAR}",
    )
)
OUT.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    season = core.season_label(START_YEAR)
    tasks = [(season, team_id) for team_id in core.TEAM_IDS]
    results: list[core.TeamSeasonResult] = []
    failures: list[dict[str, Any]] = []

    print(
        f"Starting checkpoint for {season}: {len(tasks)} franchise IDs, "
        f"{WORKERS} workers",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_map = {
            executor.submit(core.process_team_season, season, team_id): team_id
            for _, team_id in tasks
        }
        completed = 0
        for future in as_completed(future_map):
            team_id = future_map[future]
            completed += 1
            try:
                result = future.result()
                results.append(result)
                print(
                    f"[{completed}/{len(tasks)}] OK {season} {team_id} "
                    f"{result.team_abbreviation or 'inactive'} "
                    f"lineups={result.lineup_rows}",
                    flush=True,
                )
            except Exception as exc:
                failure = {"season": season, "team_id": team_id, "error": repr(exc)}
                failures.append(failure)
                print(f"[{completed}/{len(tasks)}] FAIL {failure}", flush=True)

    results.sort(key=lambda item: item.team_id)
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
            "season",
            "team_id",
            "team",
            "lineup_rows",
            "opponent_rows",
            "team_minutes",
            "status",
        ],
    )

    if failures:
        write_csv(
            OUT / "request_failures.csv",
            failures,
            ["season", "team_id", "error"],
        )

    detail_rows = [row for result in active_results for row in result.player_rows]
    detail_rows.sort(key=lambda row: (row["team_id"], row["player_id"]))
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
        ],
    )

    expected_active = 29 if START_YEAR <= 2003 else 30
    metadata = {
        "season": season,
        "start_year": START_YEAR,
        "franchise_ids_checked": len(tasks),
        "expected_active_teams": expected_active,
        "active_teams": len(active_results),
        "inactive_franchise_ids": len(results) - len(active_results),
        "failed_team_requests": len(failures),
        "player_team_season_rows": len(detail_rows),
        "max_lineup_rows": max(
            (result.lineup_rows for result in active_results), default=0
        ),
        "workers": WORKERS,
        "method": (
            "Pair PBP Stats Type=Lineup and Type=LineupOpponent by five-player "
            "EntityId, then allocate lineup seconds and rebound totals to all five players."
        ),
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    validation_errors: list[str] = []
    if failures:
        validation_errors.append(f"{len(failures)} failed team requests")
    if len(results) != 30:
        validation_errors.append(f"only {len(results)} of 30 franchise IDs returned")
    if len(active_results) != expected_active:
        validation_errors.append(
            f"active teams={len(active_results)}, expected={expected_active}"
        )
    if len(detail_rows) < 250:
        validation_errors.append(f"too few player-team rows: {len(detail_rows)}")
    if metadata["max_lineup_rows"] >= core.MAX_ROWS:
        validation_errors.append("at least one team may be capped at 500 rows")

    validation = {
        "season": season,
        "complete": not validation_errors,
        "errors": validation_errors,
        "metadata": metadata,
    }
    (OUT / "validation.json").write_text(
        json.dumps(validation, indent=2), encoding="utf-8"
    )

    if validation_errors:
        raise SystemExit("; ".join(validation_errors))

    (OUT / "season_complete.json").write_text(
        json.dumps(
            {
                "season": season,
                "start_year": START_YEAR,
                "validated": True,
                "active_teams": len(active_results),
                "player_team_season_rows": len(detail_rows),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
