from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import repair_overlap_boundaries_v12 as base


def source_priority(event):
    if event is None:
        return -1
    priority = {
        base.CURATED: 4,
        base.OFFICIAL: 3,
        "Basketball-Reference via Internet Archive": 2,
        "RealGM league transaction history": 1,
    }
    score = priority.get(str(event.get("source_system") or ""), 0)
    if event.get("derived_boundary_type") == "source_stated_10_day_contract_expiry":
        score += 2
    return score


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        base.self_test()
        assert source_priority(None) == -1
        assert source_priority({"source_system": base.CURATED}) > source_priority({"source_system": base.OFFICIAL})
        print("OVERLAP BOUNDARY REPAIR V12 RUNTIME SELF-TEST PASSED")
        return 0

    if not base.EVENTS.exists() or not base.GAMES.exists() or not base.TARGETS.exists():
        raise RuntimeError("v11 event stream, schedules, and overlap diagnostic are required")

    events = base.read_jsonl_gz(base.EVENTS)
    before = len(events)
    targets = base.target_keys()
    games = base.team_game_index(base.read_jsonl_gz(base.GAMES))

    bubble = base.fix_2020_bubble_season(events)
    events, same_move = base.collapse_official_trade_duplicates(events, targets)
    events, different_move = base.collapse_conflicting_preliminary_trades(events, targets)
    events, voided = base.remove_voided_trades(events, targets)
    hist_added, hist_audit = base.rebuild_historical_10day_endpoints(events, games)
    verified_added, verified_removed = base.apply_verified_supplement(events)

    best = {}
    for event in events:
        key = base.dedupe_key(event)
        old = best.get(key)
        if old is None or source_priority(event) > source_priority(old):
            best[key] = event
    events = list(best.values())
    base.write_jsonl_gz(base.EVENTS, events)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_events": before,
        "output_events": len(events),
        "bubble_official_events_reassigned_to_2019_20": len(bubble),
        "duplicate_same_move_official_trades_removed": len(same_move),
        "conflicting_preliminary_official_trades_removed": len(different_move),
        "voided_trade_chains_removed": len(voided),
        "historical_10day_endpoints_rebuilt": hist_added,
        "verified_supplement_events_added": verified_added,
        "superseded_james_ennis_derived_endpoints_removed": len(verified_removed),
        "bubble_audit_sample": bubble[:100],
        "same_move_trade_audit": same_move,
        "different_move_trade_audit": different_move,
        "voided_trade_audit": voided,
        "historical_10day_audit_sample": hist_audit[:100],
        "policy": (
            "No QA gate is weakened. Targeted duplicate NBA movement records are collapsed to one finalized transaction record; "
            "voided trades are suppressed only when the original official source team later records the player's departure without reacquisition; "
            "historical 10-day endpoints defer to explicit source-stated expiries; and a small verified supplement supplies exact boundaries "
            "for source gaps documented in transaction histories/team releases. July-August 2020 movement rows are assigned to the still-active 2019-20 regular season."
        ),
    }
    base.SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_sample") and not k.endswith("_audit")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
