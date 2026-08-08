from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
WINDOWS = ROOT / "player_team_season_windows.jsonl.gz"
SUMMARY = ROOT / "zero_minute_official_summary.json"

SEASON_BOUNDS = {
    "2015-16": ("2015-10-27", "2016-04-13"), "2016-17": ("2016-10-25", "2017-04-12"),
    "2017-18": ("2017-10-17", "2018-04-11"), "2018-19": ("2018-10-16", "2019-04-10"),
    "2019-20": ("2019-10-22", "2020-08-14"), "2020-21": ("2020-12-22", "2021-05-16"),
    "2021-22": ("2021-10-19", "2022-04-10"), "2022-23": ("2022-10-18", "2023-04-09"),
    "2023-24": ("2023-10-24", "2024-04-14"), "2024-25": ("2024-10-22", "2025-04-13"),
    "2025-26": ("2025-10-21", "2026-04-12"),
}


def load_jsonl_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    windows = list(load_jsonl_gz(WINDOWS))
    existing = {(w["season"], str(w["player_id"]), int(w["team_id"])) for w in windows}

    grouped = defaultdict(list)
    for e in load_jsonl_gz(EVENTS):
        if e.get("source_system") != "Official NBA Player Movement feed":
            continue
        season = e.get("season")
        day = e.get("exact_date")
        pid = str(e.get("player_id") or "")
        if season not in SEASON_BOUNDS or not pid or not day:
            continue
        start, end = SEASON_BOUNDS[season]
        if not (start <= day <= end):
            continue
        for field in ("source_team_id", "destination_team_id"):
            team = e.get(field)
            if team:
                grouped[(season, pid, int(team))].append(e)

    added = []
    for key, events in sorted(grouped.items()):
        if key in existing:
            continue
        season, pid, team = key
        season_start, season_end = SEASON_BOUNDS[season]
        acquisitions = sorted(
            (e for e in events if e.get("destination_team_id") == team and e.get("event_type") in {"trade", "acquire", "claim"}),
            key=lambda e: e["exact_date"],
        )
        departures = sorted(
            (e for e in events if e.get("source_team_id") == team and e.get("event_type") in {"trade", "depart"}),
            key=lambda e: e["exact_date"],
        )
        start_event = acquisitions[0] if acquisitions else None
        end_event = departures[-1] if departures else None
        tenure_start = start_event["exact_date"] if start_event else season_start
        tenure_end = end_event["exact_date"] if end_event else season_end
        if tenure_start > tenure_end:
            continue
        exemplar = start_event or end_event or events[0]
        refs = sorted({str(e.get("source_reference") or "") for e in events if e.get("source_reference")})
        row = {
            "season": season,
            "player_id": pid,
            "player_name": exemplar.get("player_name") or pid,
            "team_id": team,
            "team_abbr": str(team),
            "tenure_start": tenure_start,
            "tenure_end": tenure_end,
            "start_reason": f"{start_event.get('event_type')}_into_team" if start_event else "season_open_roster_inferred_from_official_departure",
            "end_reason": f"{end_event.get('event_type')}_out_of_team" if end_event else "season_close_roster_inferred_from_official_acquisition",
            "start_source": "Official NBA Player Movement feed",
            "end_source": "Official NBA Player Movement feed",
            "start_source_reference": start_event.get("source_reference") if start_event else (refs[0] if refs else None),
            "end_source_reference": end_event.get("source_reference") if end_event else (refs[-1] if refs else None),
            "confidence": "provisional_high",
            "same_day_resolution": "transaction_date_boundary; game-day inclusion requires schedule/time audit",
            "team_games_in_window": None,
            "audit_flags": ["official_transaction_derived_affiliation", "zero_core_minutes_candidate", "same_day_game_check_required"],
        }
        windows.append(row)
        added.append(row)

    windows.sort(key=lambda w: (w["season"], int(str(w["player_id"])), int(w["team_id"])))
    with gzip.open(WINDOWS, "wt", encoding="utf-8") as f:
        for row in windows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "added_windows": len(added),
        "total_windows_after_augmentation": len(windows),
        "seasons": sorted({w["season"] for w in added}),
        "note": "Adds only in-regular-season official NBA movement affiliations absent from the core; offseason-only transactions are excluded.",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
