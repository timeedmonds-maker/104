from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
WINDOWS = ROOT / "player_team_season_windows.jsonl.gz"
SUMMARY = ROOT / "roster_window_reconciliation_summary.json"

STRUCTURAL_FLAGS = {
    "multi_team_affiliation_without_acquisition_boundary",
    "multi_team_affiliation_without_departure_boundary",
}


def read_rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (
        str(r.get("season") or ""), int(str(r.get("player_id") or 0)),
        str(r.get("tenure_start") or ""), str(r.get("tenure_end") or ""), int(r.get("team_id") or 0),
    ))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def next_day(day: str) -> str:
    return (datetime.fromisoformat(day).date() + timedelta(days=1)).isoformat()


def effective_start(row: dict[str, Any]) -> str:
    start = str(row.get("tenure_start") or "")
    reason = str(row.get("start_reason") or "")
    return next_day(start) if reason.endswith("_into_team") else start


def effective_end(row: dict[str, Any]) -> str:
    return str(row.get("tenure_end") or "")


def group_has_strict_overlap(group: list[dict[str, Any]]) -> bool:
    intervals = sorted(
        [(effective_start(r), effective_end(r), int(r.get("team_id") or 0), r) for r in group],
        key=lambda x: (x[0], x[1], x[2]),
    )
    for i, (ls, le, lt, _) in enumerate(intervals):
        if not ls or not le or ls > le:
            return True
        for rs, re, rt, _ in intervals[i + 1:]:
            if rs > le:
                break
            if lt != rt and rs <= le:
                return True
    return False


def direct_boundary_coverage(row: dict[str, Any]) -> tuple[bool, bool]:
    start_reason = str(row.get("start_reason") or "")
    end_reason = str(row.get("end_reason") or "")
    start_exact = start_reason == "season_open_roster_continuity" or start_reason.endswith("_into_team") or start_reason.startswith("multi_stint_segment_start") or start_reason.startswith("season_open_roster_inferred_from_official_departure")
    end_exact = end_reason == "season_close_roster_continuity" or end_reason.endswith("_out_of_team") or end_reason.startswith("multi_stint_segment_end") or end_reason.startswith("season_close_roster_inferred_from_official_acquisition")
    return start_exact, end_exact


def reconcile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("season") or ""), str(row.get("player_id") or ""))].append(row)

    cleared_windows = 0
    cleared_flags = 0
    exact_groups = 0
    unresolved_groups = 0

    for _, group in grouped.items():
        if len({int(r.get("team_id") or 0) for r in group}) <= 1:
            continue

        overlap = group_has_strict_overlap(group)
        all_boundaries_typed = all(all(direct_boundary_coverage(r)) for r in group)
        if overlap or not all_boundaries_typed:
            unresolved_groups += 1
            continue

        exact_groups += 1
        for row in group:
            flags = list(row.get("audit_flags") or [])
            before = len(flags)
            flags = [flag for flag in flags if flag not in STRUCTURAL_FLAGS and flag != "invalid_boundary_order"]
            removed = before - len(flags)
            if removed:
                cleared_flags += removed
                cleared_windows += 1
                row["audit_flags"] = sorted(set(flags + ["multi_team_structure_reconciled_exactly"]))
                if str(row.get("confidence") or "").casefold() == "review":
                    row["confidence"] = "provisional_high" if (
                        str(row.get("start_reason") or "").endswith("_into_team")
                        or str(row.get("end_reason") or "").endswith("_out_of_team")
                        or "multi_stint_segment" in row["audit_flags"]
                    ) else "high"
                row["structural_reconciliation"] = "exact_nonoverlapping_multi-team chronology"

    write_rows(WINDOWS, rows)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_count": len(rows),
        "multi_team_player_season_groups": sum(len({int(r.get('team_id') or 0) for r in g}) > 1 for g in grouped.values()),
        "exact_nonoverlapping_groups": exact_groups,
        "unresolved_multi_team_groups": unresolved_groups,
        "windows_with_structural_flags_cleared": cleared_windows,
        "structural_flags_cleared": cleared_flags,
        "remaining_review_windows": sum(str(r.get("confidence") or "").casefold() == "review" for r in rows),
        "remaining_structural_flag_windows": sum(bool(set(r.get("audit_flags") or []) & STRUCTURAL_FLAGS) for r in rows),
        "policy": "Only clears structural-review flags when effective roster intervals are already exact and non-overlapping; no dates are invented or shifted by this step.",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main() -> int:
    rows = read_rows(WINDOWS)
    reconcile(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
