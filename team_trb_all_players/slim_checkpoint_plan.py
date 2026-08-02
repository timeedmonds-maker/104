from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

TEAM_IDS = [
    1610612737, 1610612738, 1610612739, 1610612740, 1610612741,
    1610612742, 1610612743, 1610612744, 1610612745, 1610612746,
    1610612747, 1610612748, 1610612749, 1610612750, 1610612751,
    1610612752, 1610612753, 1610612754, 1610612755, 1610612756,
    1610612757, 1610612758, 1610612759, 1610612760, 1610612761,
    1610612762, 1610612763, 1610612764, 1610612765, 1610612766,
]
CHECKPOINT_ROOT = Path(os.getenv(
    "PBPSTATS_CHECKPOINT_ROOT",
    "team_trb_all_players/checkpoints",
))
FINAL_MARKER = Path(os.getenv(
    "PBPSTATS_FINAL_MARKER",
    "team_trb_all_players/full_career_output/aggregate_complete.json",
))
BATCH_SIZE = max(1, min(200, int(os.getenv("PBPSTATS_BATCH_SIZE", "20"))))
EXPECTED_TASKS = 26 * len(TEAM_IDS)


def season_dates(start_year: int) -> tuple[str, str]:
    if start_year == 2019:
        return "2019-10-01", "2020-08-31"
    if start_year == 2020:
        return "2020-12-01", "2021-05-31"
    return f"{start_year}-10-01", f"{start_year + 1}-04-30"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def checkpoint_complete(season: str, team_id: int) -> bool:
    directory = CHECKPOINT_ROOT / season / str(team_id)
    required = {
        "batch_complete.json",
        "batch_metadata.json",
        "player_team_batch.csv",
        "team_status.csv",
    }
    if not all((directory / name).exists() for name in required):
        return False

    complete = read_json(directory / "batch_complete.json")
    metadata = read_json(directory / "batch_metadata.json")
    if complete.get("complete") is not True or metadata.get("complete") is not True:
        return False
    if str(complete.get("season")) != season or str(metadata.get("season")) != season:
        return False
    team_ids = metadata.get("team_ids")
    if team_ids != [team_id]:
        return False
    if int(metadata.get("teams_expected", 0)) != 1 or int(metadata.get("teams_validated", 0)) != 1:
        return False

    try:
        with (directory / "team_status.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return False
    return (
        len(rows) == 1
        and str(rows[0].get("season")) == season
        and str(rows[0].get("team_id")) == str(team_id)
        and str(rows[0].get("validated", "")).lower() == "true"
    )


def final_complete() -> bool:
    marker = read_json(FINAL_MARKER)
    return (
        marker.get("complete") is True
        and int(marker.get("team_season_checkpoints", 0)) == EXPECTED_TASKS
    )


def write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> None:
    tasks: list[dict[str, str]] = []
    complete_count = 0
    for start_year in range(2000, 2026):
        season = f"{start_year}-{str(start_year + 1)[-2:]}"
        from_date, to_date = season_dates(start_year)
        for team_id in TEAM_IDS:
            if checkpoint_complete(season, team_id):
                complete_count += 1
                continue
            tasks.append({
                "season": season,
                "from_date": from_date,
                "to_date": to_date,
                "team_id": str(team_id),
                "group": str(team_id),
                "team_ids": str(team_id),
                "task_key": f"{season}-{team_id}",
            })

    selected = tasks[:BATCH_SIZE]
    pending_total = len(tasks)
    done = pending_total == 0 and final_complete()
    needs_aggregate = pending_total == 0 and not done
    matrix_include = selected or [{
        "season": "none",
        "from_date": "2000-01-01",
        "to_date": "2000-01-01",
        "team_id": "0",
        "group": "0",
        "team_ids": "0",
        "task_key": "noop",
    }]
    matrix = json.dumps({"include": matrix_include}, separators=(",", ":"))

    write_output("matrix", matrix)
    write_output("has_work", "true" if selected else "false")
    write_output("needs_aggregate", "true" if needs_aggregate else "false")
    write_output("done", "true" if done else "false")
    write_output("complete_count", str(complete_count))
    write_output("pending_total", str(pending_total))
    write_output("selected_count", str(len(selected)))
    print(
        f"CHECKPOINT PLAN complete={complete_count}/{EXPECTED_TASKS} "
        f"pending={pending_total} selected={len(selected)} aggregate={needs_aggregate} done={done}",
        flush=True,
    )


if __name__ == "__main__":
    main()
