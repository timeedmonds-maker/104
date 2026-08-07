from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
INPUT = ROOT / "player_team_season_windows_evidence_audited.jsonl.gz"
QUEUE_JSON = ROOT / "tenure_review_queue.json"
QUEUE_CSV = ROOT / "tenure_review_queue.csv"
SUMMARY = ROOT / "tenure_review_queue_summary.json"


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def classify(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if row.get("schedule_boundary_status") == "needs_ordering_evidence":
        reasons.append("same_day_ordering_evidence")
    if str(row.get("confidence") or "").casefold() == "review":
        reasons.append("review_confidence")
    flags = set(str(x) for x in (row.get("audit_flags") or []))
    structural = {
        "invalid_boundary_order",
        "multi_team_affiliation_without_acquisition_boundary",
        "multi_team_affiliation_without_departure_boundary",
    }
    if flags & structural:
        reasons.append("structural_boundary_gap")
    return reasons


def queue_item(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "season": row.get("season"),
        "player_id": str(row.get("player_id") or ""),
        "player_name": row.get("player_name"),
        "team_id": row.get("team_id"),
        "team_abbr": row.get("team_abbr"),
        "tenure_start": row.get("tenure_start"),
        "tenure_end": row.get("tenure_end"),
        "start_reason": row.get("start_reason"),
        "end_reason": row.get("end_reason"),
        "start_source": row.get("start_source"),
        "end_source": row.get("end_source"),
        "start_source_reference": row.get("start_source_reference"),
        "end_source_reference": row.get("end_source_reference"),
        "confidence": row.get("confidence"),
        "schedule_boundary_status": row.get("schedule_boundary_status"),
        "boundary_game_ids": row.get("boundary_game_ids") or [],
        "same_day_positive_participation_game_ids": row.get("same_day_positive_participation_game_ids") or [],
        "same_day_unresolved_game_ids": row.get("same_day_unresolved_game_ids") or [],
        "team_games_min": row.get("team_games_min"),
        "team_games_max": row.get("team_games_max"),
        "audit_flags": row.get("audit_flags") or [],
        "review_reasons": reasons,
    }


def build_queue(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for row in rows:
        reasons = classify(row)
        if reasons:
            queue.append(queue_item(row, reasons))

    queue.sort(key=lambda r: (
        str(r.get("season") or ""),
        str(r.get("player_name") or ""),
        int(r.get("team_id") or 0),
        str(r.get("tenure_start") or ""),
    ))
    counts = Counter(reason for item in queue for reason in item["review_reasons"])
    same_day_games = sorted({
        str(game_id)
        for item in queue
        if "same_day_ordering_evidence" in item["review_reasons"]
        for game_id in item.get("same_day_unresolved_game_ids") or item.get("boundary_game_ids") or []
        if str(game_id)
    })
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_windows": len(rows),
        "review_queue_windows": len(queue),
        "reason_counts": dict(sorted(counts.items())),
        "unique_unresolved_same_day_games": len(same_day_games),
        "unresolved_same_day_game_ids": same_day_games,
        "stage1_exact_ready": len(queue) == 0,
        "policy": (
            "A window remains in the queue when an exact roster boundary cannot yet be "
            "proved. Injury, suspension and DNP absence are never treated as evidence "
            "that a player was off-roster."
        ),
    }
    return queue, summary


def write_outputs(queue: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    QUEUE_JSON.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    fields = [
        "season", "player_id", "player_name", "team_id", "team_abbr",
        "tenure_start", "tenure_end", "confidence", "schedule_boundary_status",
        "review_reasons", "boundary_game_ids", "same_day_unresolved_game_ids",
        "team_games_min", "team_games_max", "start_source", "end_source",
        "start_source_reference", "end_source_reference", "audit_flags",
    ]
    with QUEUE_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in queue:
            row = {key: item.get(key) for key in fields}
            for key in ("review_reasons", "boundary_game_ids", "same_day_unresolved_game_ids", "audit_flags"):
                row[key] = json.dumps(row.get(key) or [], ensure_ascii=False)
            writer.writerow(row)
    summary = dict(summary)
    summary.update({
        "queue_json": str(QUEUE_JSON),
        "queue_csv": str(QUEUE_CSV),
    })
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def self_test() -> None:
    rows = [
        {
            "season": "2023-24", "player_id": "1", "player_name": "Resolved",
            "team_id": 10, "confidence": "high", "schedule_boundary_status": "resolved",
            "audit_flags": [],
        },
        {
            "season": "2023-24", "player_id": "2", "player_name": "Same Day",
            "team_id": 20, "confidence": "provisional_high",
            "schedule_boundary_status": "needs_ordering_evidence",
            "boundary_game_ids": ["0022300001"],
            "same_day_unresolved_game_ids": ["0022300001"],
            "audit_flags": ["same_day_game_ordering_evidence_required"],
        },
        {
            "season": "2003-04", "player_id": "3", "player_name": "Gap",
            "team_id": 30, "confidence": "review", "schedule_boundary_status": "resolved",
            "audit_flags": ["multi_team_affiliation_without_departure_boundary"],
        },
    ]
    queue, summary = build_queue(rows)
    assert len(queue) == 2
    assert summary["reason_counts"]["same_day_ordering_evidence"] == 1
    assert summary["reason_counts"]["review_confidence"] == 1
    assert summary["reason_counts"]["structural_boundary_gap"] == 1
    assert summary["unique_unresolved_same_day_games"] == 1
    assert summary["stage1_exact_ready"] is False
    print("build_roster_tenure_review_queue self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    rows = read_rows(INPUT)
    queue, summary = build_queue(rows)
    write_outputs(queue, summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
