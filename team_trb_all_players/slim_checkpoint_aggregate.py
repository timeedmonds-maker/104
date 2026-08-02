from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "team_trb_all_players/checkpoints")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "team_trb_all_players/full_career_output")
OUT.mkdir(parents=True, exist_ok=True)
TEAM_IDS = [
    1610612737, 1610612738, 1610612739, 1610612740, 1610612741,
    1610612742, 1610612743, 1610612744, 1610612745, 1610612746,
    1610612747, 1610612748, 1610612749, 1610612750, 1610612751,
    1610612752, 1610612753, 1610612754, 1610612755, 1610612756,
    1610612757, 1610612758, 1610612759, 1610612760, 1610612761,
    1610612762, 1610612763, 1610612764, 1610612765, 1610612766,
]
EXPECTED_KEYS = {
    (f"{year}-{str(year + 1)[-2:]}", str(team_id))
    for year in range(2000, 2026)
    for team_id in TEAM_IDS
}
MIN_MINUTES = 10_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite value {value!r}")
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    metadata_paths = sorted(ROOT.glob("*/*/batch_metadata.json"))
    seen_keys: set[tuple[str, str]] = set()
    duplicate_keys: list[tuple[str, str]] = []
    problems: list[str] = []
    checkpoint_rows: list[dict[str, Any]] = []

    for path in metadata_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        season = str(data.get("season") or "")
        team_ids = data.get("team_ids")
        team_id = str(team_ids[0]) if isinstance(team_ids, list) and len(team_ids) == 1 else ""
        key = (season, team_id)
        if key in seen_keys:
            duplicate_keys.append(key)
        seen_keys.add(key)
        checkpoint_rows.append({
            "season": season,
            "team_id": team_id,
            "complete": bool(data.get("complete")),
            "teams_validated": data.get("teams_validated"),
            "teams_expected": data.get("teams_expected"),
            "player_team_rows": data.get("player_team_rows"),
            "request_attempts": data.get("request_attempts"),
            "manifest_rows": data.get("manifest_rows"),
        })
        if data.get("complete") is not True:
            problems.append(f"incomplete checkpoint {key}")

    missing = sorted(EXPECTED_KEYS - seen_keys)
    unexpected = sorted(seen_keys - EXPECTED_KEYS)
    if duplicate_keys:
        problems.append(f"duplicate checkpoints: {duplicate_keys[:20]}")
    if missing:
        problems.append(f"missing {len(missing)} checkpoints; first={missing[:20]}")
    if unexpected:
        problems.append(f"unexpected checkpoints: {unexpected[:20]}")

    status_paths = sorted(ROOT.glob("*/*/team_status.csv"))
    player_paths = sorted(ROOT.glob("*/*/player_team_batch.csv"))
    team_statuses = [row for path in status_paths for row in read_csv(path)]
    invalid_teams = [row for row in team_statuses if row.get("validated", "").lower() != "true"]
    if invalid_teams:
        problems.append(f"{len(invalid_teams)} team-season validations failed")
    if len(team_statuses) != len(EXPECTED_KEYS):
        problems.append(
            f"expected {len(EXPECTED_KEYS)} team-status rows, found {len(team_statuses)}"
        )

    player_team_rows = [row for path in player_paths for row in read_csv(path)]
    write_csv(OUT / "all_player_team_seasons.csv", player_team_rows)
    write_csv(OUT / "all_team_status.csv", team_statuses)
    write_csv(OUT / "checkpoint_status.csv", checkpoint_rows)

    if problems:
        (OUT / "aggregate_diagnostics.json").write_text(
            json.dumps({
                "generated_at_utc": now(),
                "complete": False,
                "problems": problems,
                "checkpoint_files": len(metadata_paths),
                "team_status_rows": len(team_statuses),
                "player_team_rows": len(player_team_rows),
            }, indent=2),
            encoding="utf-8",
        )
        return 1

    careers: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "names": Counter(),
        "seconds": 0.0,
        "team_rebounds": 0.0,
        "opponent_rebounds": 0.0,
        "seasons": set(),
        "teams": set(),
    })
    for row in player_team_rows:
        player_id = row["player_id"]
        seconds = num(row["seconds"])
        item = careers[player_id]
        item["names"][row["player"]] += seconds
        item["seconds"] += seconds
        item["team_rebounds"] += num(row["team_rebounds"])
        item["opponent_rebounds"] += num(row["opponent_rebounds"])
        item["seasons"].add(row["season"])
        item["teams"].add(row["team_id"])

    all_players: list[dict[str, Any]] = []
    for player_id, item in careers.items():
        total_rebounds = item["team_rebounds"] + item["opponent_rebounds"]
        all_players.append({
            "player_id": player_id,
            "player": item["names"].most_common(1)[0][0],
            "minutes": item["seconds"] / 60,
            "seconds": item["seconds"],
            "team_rebounds": item["team_rebounds"],
            "opponent_rebounds": item["opponent_rebounds"],
            "team_trb_pct": (
                100 * item["team_rebounds"] / total_rebounds if total_rebounds else ""
            ),
            "season_count": len(item["seasons"]),
            "team_count": len(item["teams"]),
            "seasons": ",".join(sorted(item["seasons"])),
            "team_ids": ",".join(sorted(item["teams"])),
        })

    all_players.sort(
        key=lambda row: (-num(row["team_trb_pct"]), -num(row["minutes"]), row["player"])
    )
    qualifying = [row.copy() for row in all_players if num(row["minutes"]) >= MIN_MINUTES]
    for rank, row in enumerate(qualifying, start=1):
        row["rank"] = rank
    qualifying = [{"rank": row.pop("rank"), **row} for row in qualifying]

    write_csv(OUT / "all_player_career_team_trb.csv", all_players)
    write_csv(OUT / "career_team_trb_10000_minutes.csv", qualifying)
    (OUT / "aggregate_complete.json").write_text(
        json.dumps({
            "generated_at_utc": now(),
            "complete": True,
            "team_season_checkpoints": len(metadata_paths),
            "team_status_rows": len(team_statuses),
            "player_team_rows": len(player_team_rows),
            "career_players": len(all_players),
            "qualifying_players": len(qualifying),
            "minimum_minutes": MIN_MINUTES,
        }, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
