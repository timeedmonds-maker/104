from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
INPUT = ROOT / "player_team_season_windows_evidence_audited.jsonl.gz"
AUDIT = ROOT / "tenure_consistency_audit.json"
SUMMARY = ROOT / "tenure_consistency_summary.json"


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def window_key(row: dict[str, Any]) -> tuple[str, str, int, str, str]:
    return (
        str(row.get("season") or ""),
        str(row.get("player_id") or ""),
        int(row.get("team_id") or 0),
        str(row.get("tenure_start") or ""),
        str(row.get("tenure_end") or ""),
    )


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    invalid_intervals: list[dict[str, Any]] = []
    resolved_game_count_inconsistencies: list[dict[str, Any]] = []
    duplicate_windows: list[dict[str, Any]] = []
    strict_cross_team_overlaps: list[dict[str, Any]] = []
    strict_same_team_overlaps: list[dict[str, Any]] = []
    same_day_cross_team_touches: list[dict[str, Any]] = []

    key_counts = Counter(window_key(row) for row in rows)
    for key, count in key_counts.items():
        if count > 1:
            duplicate_windows.append({
                "season": key[0], "player_id": key[1], "team_id": key[2],
                "tenure_start": key[3], "tenure_end": key[4], "count": count,
            })

    by_player_season: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        season = str(row.get("season") or "")
        player_id = str(row.get("player_id") or "")
        start = str(row.get("tenure_start") or "")
        end = str(row.get("tenure_end") or "")
        if not season or not player_id or not start or not end or start > end:
            invalid_intervals.append(row)
            continue

        if row.get("schedule_boundary_status") == "resolved":
            exact = row.get("team_games_in_window")
            lo = row.get("team_games_min")
            hi = row.get("team_games_max")
            if exact is None or lo is None or hi is None or not (int(exact) == int(lo) == int(hi)):
                resolved_game_count_inconsistencies.append(row)

        by_player_season[(season, player_id)].append(row)

    for (season, player_id), group in by_player_season.items():
        ordered = sorted(
            group,
            key=lambda row: (
                str(row["tenure_start"]), str(row["tenure_end"]), int(row.get("team_id") or 0)
            ),
        )
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                if str(right["tenure_start"]) > str(left["tenure_end"]):
                    break
                left_team = int(left.get("team_id") or 0)
                right_team = int(right.get("team_id") or 0)
                left_end = str(left["tenure_end"])
                right_start = str(right["tenure_start"])
                record = {
                    "season": season,
                    "player_id": player_id,
                    "player_name": left.get("player_name") or right.get("player_name"),
                    "left_team_id": left_team,
                    "left_start": left.get("tenure_start"),
                    "left_end": left_end,
                    "right_team_id": right_team,
                    "right_start": right_start,
                    "right_end": right.get("tenure_end"),
                    "left_confidence": left.get("confidence"),
                    "right_confidence": right.get("confidence"),
                }
                if right_start < left_end:
                    if left_team == right_team:
                        strict_same_team_overlaps.append(record)
                    else:
                        strict_cross_team_overlaps.append(record)
                elif right_start == left_end and left_team != right_team:
                    same_day_cross_team_touches.append(record)

    confidence_counts = Counter(str(row.get("confidence") or "unknown") for row in rows)
    boundary_status_counts = Counter(str(row.get("schedule_boundary_status") or "unknown") for row in rows)
    unresolved = [
        row for row in rows
        if row.get("schedule_boundary_status") == "needs_ordering_evidence"
    ]

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_count": len(rows),
        "player_seasons": len(by_player_season),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "boundary_status_counts": dict(sorted(boundary_status_counts.items())),
        "invalid_interval_count": len(invalid_intervals),
        "duplicate_window_count": len(duplicate_windows),
        "resolved_game_count_inconsistency_count": len(resolved_game_count_inconsistencies),
        "strict_cross_team_overlap_count": len(strict_cross_team_overlaps),
        "strict_same_team_overlap_count": len(strict_same_team_overlaps),
        "same_day_cross_team_touch_count": len(same_day_cross_team_touches),
        "remaining_same_day_unresolved_count": len(unresolved),
        "invalid_intervals": invalid_intervals,
        "duplicate_windows": duplicate_windows,
        "resolved_game_count_inconsistencies": resolved_game_count_inconsistencies,
        "strict_cross_team_overlaps": strict_cross_team_overlaps,
        "strict_same_team_overlaps": strict_same_team_overlaps,
        "same_day_cross_team_touches": same_day_cross_team_touches,
        "remaining_same_day_unresolved": unresolved,
    }


def build() -> dict[str, Any]:
    rows = read_rows(INPUT)
    audit = audit_rows(rows)
    ROOT.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {k: v for k, v in audit.items() if not isinstance(v, list)}
    summary["audit"] = str(AUDIT)
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def self_test() -> None:
    good = [
        {
            "season": "2023-24", "player_id": "1", "player_name": "Trade Player",
            "team_id": 10, "tenure_start": "2023-10-24", "tenure_end": "2024-02-01",
            "confidence": "provisional_high", "schedule_boundary_status": "resolved",
            "team_games_in_window": 48, "team_games_min": 48, "team_games_max": 48,
        },
        {
            "season": "2023-24", "player_id": "1", "player_name": "Trade Player",
            "team_id": 20, "tenure_start": "2024-02-01", "tenure_end": "2024-04-14",
            "confidence": "provisional_high", "schedule_boundary_status": "resolved",
            "team_games_in_window": 35, "team_games_min": 35, "team_games_max": 35,
        },
    ]
    result = audit_rows(good)
    assert result["invalid_interval_count"] == 0
    assert result["duplicate_window_count"] == 0
    assert result["strict_cross_team_overlap_count"] == 0
    assert result["same_day_cross_team_touch_count"] == 1
    assert result["resolved_game_count_inconsistency_count"] == 0

    bad = good + [{
        "season": "2023-24", "player_id": "1", "player_name": "Trade Player",
        "team_id": 30, "tenure_start": "2024-01-20", "tenure_end": "2024-03-01",
        "confidence": "review", "schedule_boundary_status": "needs_ordering_evidence",
        "team_games_in_window": None, "team_games_min": 10, "team_games_max": 11,
    }]
    bad_result = audit_rows(bad)
    assert bad_result["strict_cross_team_overlap_count"] >= 1
    print("audit_roster_tenure_consistency self-test PASSED")


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
