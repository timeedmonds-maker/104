from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import normalize_roster_transactions as norm
import recover_overlap_events_from_bref_v12 as base

BASE = Path(__file__).resolve().parent
DB = BASE / "impact_database"
ROSTER = DB / "roster_tenure"
BREF = DB / "historical_transactions" / "basketball_reference_uniform" / "season_rows"
EVENTS = ROSTER / "normalized_transactions.jsonl.gz"
TARGETS_V12 = ROSTER / "remaining_overlap_event_chains_v12.json"
TARGETS_V11 = ROSTER / "remaining_overlap_event_chains_v11.json"
SUMMARY = ROSTER / "bref_targeted_overlap_recovery_v13_summary.json"


def targets_path() -> Path:
    return TARGETS_V12 if TARGETS_V12.exists() else TARGETS_V11


def target_index(path: Path) -> tuple[dict[str, list[dict[str, Any]]], int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in data.get("cases") or []:
        teams = {int(w["team_id"]) for w in case.get("windows") or [] if w.get("team_id")}
        by_season[str(case["season"])].append({
            "season": str(case["season"]),
            "player_id": str(case["player_id"]),
            "player_name": str(case.get("player_name") or case["player_id"]),
            "teams": teams,
        })
    return dict(by_season), sum(len(v) for v in by_season.values())


def parser_ready_row(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Collapse all description cells into the single text cell expected by the legacy parser.

    The cached BRef archive sometimes stores the transaction description across multiple cells.
    v12 correctly detected target player links by joining cells[1:], but then passed the original
    row to normalize_roster_transactions.parse_bref_row(), which only reads cells[1]. That is why
    rows such as D.J. White, Richard Jefferson and J.J. Redick were detected but left unparsed.
    """
    cells = list(row.get("cells") or [])
    raw_text = " ".join(str(x) for x in cells[1:]).strip()
    prepared = dict(row)
    prepared["cells"] = [cells[0] if cells else "", raw_text]
    return prepared, raw_text


def recover() -> dict[str, Any]:
    target_file = targets_path()
    if not EVENTS.exists() or not target_file.exists():
        raise RuntimeError("normalized events and a durable overlap diagnostic are required")

    context = norm.load_core_context()
    targets, target_count = target_index(target_file)
    events = base.read_jsonl_gz(EVENTS)
    seen = {base.dedupe_key(e) for e in events if e.get("player_id")}

    recovered: list[dict[str, Any]] = []
    rows_scanned = 0
    target_rows_seen = 0
    parser_empty_rows = 0
    target_rows_still_unparsed: list[dict[str, Any]] = []

    for season, season_targets in sorted(targets.items()):
        path = BREF / f"{season}.jsonl.gz"
        if not path.exists():
            continue
        for row in base.read_jsonl_gz(path):
            rows_scanned += 1
            prepared, raw_text = parser_ready_row(row)
            if not raw_text:
                continue

            linked_targets: list[dict[str, Any]] = []
            for link in norm.bref_player_links(row):
                name = str(link.get("text") or "")
                if not name or base.is_future_pick_mention(name, raw_text):
                    continue
                for target in season_targets:
                    if base.compatible_name(name, target["player_name"]):
                        linked_targets.append(target)
                        break
            if not linked_targets:
                continue
            target_rows_seen += 1

            parsed_events = [base.event_dict(e) for e in norm.parse_bref_row(prepared, context)]
            if not parsed_events:
                parser_empty_rows += 1
            matched_any = False
            for parsed in parsed_events:
                pname = str(parsed.get("player_name") or "")
                if not pname or base.is_future_pick_mention(pname, raw_text):
                    continue
                parsed["source_team_id"] = base.charlotte_fix(
                    int(parsed.get("source_team_id")) if parsed.get("source_team_id") else None,
                    parsed.get("source_team_name"), season,
                )
                parsed["destination_team_id"] = base.charlotte_fix(
                    int(parsed.get("destination_team_id")) if parsed.get("destination_team_id") else None,
                    parsed.get("destination_team_name"), season,
                )
                target = base.choose_target(
                    season,
                    pname,
                    int(parsed.get("source_team_id")) if parsed.get("source_team_id") else None,
                    int(parsed.get("destination_team_id")) if parsed.get("destination_team_id") else None,
                    targets,
                )
                if target is None:
                    continue
                parsed["player_id"] = target["player_id"]
                parsed["identity_resolution"] = "v13_full_bref_row+target_player_link+overlap_case"
                parsed["confidence"] = "high" if (parsed.get("source_team_id") or parsed.get("destination_team_id")) else "review"
                key = base.dedupe_key(parsed)
                if key not in seen:
                    seen.add(key)
                    recovered.append(parsed)
                matched_any = True

            if not matched_any:
                target_rows_still_unparsed.append({
                    "season": season,
                    "row_index": row.get("row_index"),
                    "source_url": row.get("source_url"),
                    "raw_text": raw_text,
                    "target_players": sorted({t["player_name"] for t in linked_targets}),
                    "parser_event_count": len(parsed_events),
                })

    events.extend(recovered)
    base.write_jsonl_gz(EVENTS, events)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "target_diagnostic": target_file.name,
        "target_player_seasons": target_count,
        "cached_bref_rows_scanned": rows_scanned,
        "rows_containing_target_player_links": target_rows_seen,
        "rows_where_legacy_parser_still_returned_zero_events": parser_empty_rows,
        "recovered_target_events_added": len(recovered),
        "target_rows_still_unparsed": len(target_rows_still_unparsed),
        "recovered_event_sample": recovered[:100],
        "unparsed_target_row_sample": target_rows_still_unparsed[:100],
        "policy": (
            "Recovery remains restricted to player-season cases in the latest durable overlap diagnostic. "
            "The only parser change is to collapse cached Basketball-Reference description cells into the full transaction text before using the existing validated transaction parser. "
            "Player identity still requires the BRef player link and a unique overlap-case match; no transaction dates are invented."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_sample")}, indent=2), flush=True)
    return summary


def self_test() -> None:
    row = {
        "cells": ["February 24, 2011", "ignored auxiliary cell", "The Charlotte Bobcats traded Nazr Mohammed to the Oklahoma City Thunder for Morris Peterson and D.J. White ."],
        "links": [{"text": "D.J. White", "href": "/players/w/whitedj01.html"}],
    }
    prepared, raw = parser_ready_row(row)
    assert "D.J. White" in raw
    assert prepared["cells"][1] == raw
    assert base.compatible_name("D.J. White", "DJ White")
    assert base.compatible_name("J.J. Redick", "JJ Redick")
    print("BREF TARGETED OVERLAP RECOVERY V13 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    recover()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
