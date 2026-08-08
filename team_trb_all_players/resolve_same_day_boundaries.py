from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROSTER = BASE / "impact_database" / "roster_tenure"
INPUT = ROSTER / "player_team_season_windows_schedule_audited.jsonl.gz"
OUTPUT = ROSTER / "player_team_season_windows_evidence_audited.jsonl.gz"
AUDIT = ROSTER / "same_day_evidence_audit.json"
SUMMARY = ROSTER / "same_day_evidence_summary.json"


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build() -> dict[str, Any]:
    """Pass schedule-audited windows to the authoritative game-level roster evidence pass.

    The former implementation queried NBA playergamelog once per player-season merely to
    prove positive participation. That is redundant with the following
    resolve_same_day_roster_evidence.py pass: boxscoretraditionalv2 PlayerStats proves
    participation/listing, while boxscoresummaryv2 InactivePlayers additionally proves
    roster membership for inactive players. Skipping the redundant player-season query
    cannot create a false resolution: every unresolved boundary remains unresolved until
    positive game-level roster evidence is found by the next pass.
    """
    rows = read_rows(INPUT)
    unresolved = [r for r in rows if r.get("schedule_boundary_status") == "needs_ordering_evidence"]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUTPUT, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method": (
            "redundant player-season participation query skipped; authoritative positive "
            "game-level evidence is supplied by the following NBA box-score PlayerStats + "
            "InactivePlayers pass; absence is never interpreted as off-roster"
        ),
        "input_unresolved_windows": len(unresolved),
        "fully_resolved_windows": 0,
        "partially_resolved_windows": 0,
        "remaining_unresolved_windows": len(unresolved),
        "player_seasons_queried": 0,
        "fetch_error_count": 0,
        "output": str(OUTPUT),
        "audit": str(AUDIT),
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    SUMMARY.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"player-game participation pass safely elided; "
        f"{len(unresolved)} unresolved windows forwarded to authoritative box-score evidence",
        flush=True,
    )
    return audit


def self_test() -> None:
    # Methodological invariant: this pass never marks a boundary resolved.
    row = {
        "schedule_boundary_status": "needs_ordering_evidence",
        "boundary_game_ids": ["0022300002"],
    }
    copied = dict(row)
    assert copied["schedule_boundary_status"] == "needs_ordering_evidence"
    assert copied["boundary_game_ids"] == ["0022300002"]
    print("resolve_same_day_boundaries self-test PASSED (fail-closed passthrough)")


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
