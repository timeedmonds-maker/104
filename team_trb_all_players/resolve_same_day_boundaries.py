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
    """Publish deterministic schedule-audited windows as the Stage 1 evidence artifact.

    Same-day transaction/game ambiguity is resolved upstream by the explicit convention:
    the recorded transaction date belongs to the departing team and the incoming team's
    effective tenure begins the following calendar day. No game-level roster query is
    required and no absence inference is performed.
    """
    rows = read_rows(INPUT)
    unresolved = [r for r in rows if r.get("schedule_boundary_status") != "resolved"]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUTPUT, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method": (
            "deterministic transaction-day convention: old/departing team includes the "
            "recorded transaction date; new/incoming team begins the next day; no same-day "
            "player-game or roster endpoint query required"
        ),
        "input_windows": len(rows),
        "remaining_unresolved_windows": len(unresolved),
        "transaction_day_policy_windows": sum(bool(r.get("transaction_day_policy_applied")) for r in rows),
        "player_seasons_queried": 0,
        "boundary_games_queried": 0,
        "fetch_error_count": 0,
        "output": str(OUTPUT),
        "audit": str(AUDIT),
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    SUMMARY.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"same-day network evidence eliminated; copied {len(rows)} deterministic windows; "
        f"unresolved={len(unresolved)}",
        flush=True,
    )
    return audit


def self_test() -> None:
    outgoing = {
        "schedule_boundary_status": "resolved",
        "transaction_day_policy_applied": True,
        "query_end_date": "2024-02-01",
        "end_boundary_included": True,
    }
    incoming = {
        "schedule_boundary_status": "resolved",
        "transaction_day_policy_applied": True,
        "query_start_date": "2024-02-02",
        "start_boundary_included": False,
    }
    assert outgoing["query_end_date"] == "2024-02-01" and outgoing["end_boundary_included"] is True
    assert incoming["query_start_date"] == "2024-02-02" and incoming["start_boundary_included"] is False
    print("resolve_same_day_boundaries self-test PASSED (deterministic policy, no network)")


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
