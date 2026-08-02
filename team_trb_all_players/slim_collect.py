from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

DOWNLOAD_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "downloaded-checkpoints")
CHECKPOINT_ROOT = Path(sys.argv[2] if len(sys.argv) > 2 else "team_trb_all_players/checkpoints")
ALLOWED_FILES = {
    "batch_complete.json",
    "batch_metadata.json",
    "player_team_batch.csv",
    "team_status.csv",
    "request_audit.csv",
    "window_manifest.csv",
    "runner_result.json",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def valid_payload(directory: Path) -> tuple[bool, str, str]:
    complete = read_json(directory / "batch_complete.json")
    metadata = read_json(directory / "batch_metadata.json")
    if complete.get("complete") is not True or metadata.get("complete") is not True:
        return False, "", ""
    season = str(metadata.get("season") or "")
    team_ids = metadata.get("team_ids")
    if not season or not isinstance(team_ids, list) or len(team_ids) != 1:
        return False, "", ""
    team_id = str(team_ids[0])
    if str(complete.get("season")) != season:
        return False, "", ""
    if int(metadata.get("teams_expected", 0)) != 1 or int(metadata.get("teams_validated", 0)) != 1:
        return False, "", ""
    try:
        with (directory / "team_status.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return False, "", ""
    if not (
        len(rows) == 1
        and str(rows[0].get("season")) == season
        and str(rows[0].get("team_id")) == team_id
        and str(rows[0].get("validated", "")).lower() == "true"
    ):
        return False, "", ""
    return True, season, team_id


def write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)
    seen = 0
    copied = 0
    incomplete = 0
    copied_keys: list[str] = []

    for marker in sorted(DOWNLOAD_ROOT.glob("**/batch_metadata.json")):
        directory = marker.parent
        seen += 1
        valid, season, team_id = valid_payload(directory)
        if not valid:
            incomplete += 1
            continue
        target = CHECKPOINT_ROOT / season / team_id
        target.mkdir(parents=True, exist_ok=True)
        for name in ALLOWED_FILES:
            source = directory / name
            if source.exists() and source.is_file():
                shutil.copy2(source, target / name)
        copied += 1
        copied_keys.append(f"{season}-{team_id}")

    summary = {
        "artifacts_seen": seen,
        "complete_checkpoints_copied": copied,
        "incomplete_payloads": incomplete,
        "copied_keys": copied_keys,
    }
    print(json.dumps(summary, indent=2), flush=True)
    write_output("seen", str(seen))
    write_output("copied", str(copied))
    write_output("incomplete", str(incomplete))
    return 0


if __name__ == "__main__":
    sys.exit(main())
