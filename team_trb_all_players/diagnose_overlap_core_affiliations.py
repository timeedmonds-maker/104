from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB = BASE / "impact_database"
ROSTER = DB / "roster_tenure"
CORE = DB / "core_checkpoints"
AUDIT = ROSTER / "tenure_consistency_audit.json"
EVENTS = ROSTER / "normalized_transactions.jsonl.gz"
OUT = ROSTER / "overlap_root_diagnostic.json"


def clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if text.isdigit() else ""


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_events() -> list[dict[str, Any]]:
    rows = []
    with gzip.open(EVENTS, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    overlaps = list(audit.get("strict_cross_team_overlaps") or [])
    keys: set[tuple[str, str, int]] = set()
    player_seasons: set[tuple[str, str]] = set()
    names: dict[tuple[str, str], str] = {}
    for row in overlaps:
        season = str(row["season"])
        pid = str(row["player_id"])
        player_seasons.add((season, pid))
        names[(season, pid)] = str(row.get("player_name") or pid)
        keys.add((season, pid, int(row["left_team_id"])))
        keys.add((season, pid, int(row["right_team_id"])))

    events = read_events()
    event_index: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        if (season, pid) not in player_seasons:
            continue
        for team0 in {e.get("source_team_id"), e.get("destination_team_id")} - {None}:
            team = int(team0)
            if (season, pid, team) in keys:
                event_index[(season, pid, team)].append(e)

    rows = []
    for season, pid, team in sorted(keys):
        path = CORE / season / f"{team}.json.gz"
        seconds = None
        core_found = False
        row_excerpt: dict[str, Any] = {}
        if path.exists():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
                for r in payload.get("player_totals", []):
                    rid = clean_id(r.get("EntityId") or r.get("RowId") or r.get("PlayerId"))
                    if rid == pid:
                        core_found = True
                        seconds = number(r.get("SecondsPlayed"))
                        row_excerpt = {
                            k: r.get(k)
                            for k in ("EntityId", "RowId", "PlayerId", "Name", "ShortName", "SecondsPlayed", "GamesPlayed", "Minutes")
                            if k in r
                        }
                        break
            except Exception as exc:
                row_excerpt = {"read_error": repr(exc)}

        evs = sorted(event_index.get((season, pid, team), []), key=lambda e: (str(e.get("exact_date") or ""), str(e.get("event_type") or "")))
        relevant = []
        for e in evs:
            relevant.append({
                "exact_date": e.get("exact_date"),
                "event_type": e.get("event_type"),
                "source_team_id": e.get("source_team_id"),
                "destination_team_id": e.get("destination_team_id"),
                "source_system": e.get("source_system"),
                "raw_text": e.get("raw_text"),
                "derived_boundary_type": e.get("derived_boundary_type"),
            })
        rows.append({
            "season": season,
            "player_id": pid,
            "player_name": names[(season, pid)],
            "team_id": team,
            "core_player_row_found": core_found,
            "core_seconds_played": seconds,
            "core_zero_seconds": core_found and (seconds is None or seconds <= 0),
            "transaction_event_count": len(relevant),
            "has_transaction_support": bool(relevant),
            "core_row_excerpt": row_excerpt,
            "transaction_events": relevant,
        })

    class_counts = Counter()
    by_season = defaultdict(Counter)
    for r in rows:
        if not r["core_player_row_found"]:
            cls = "no_core_row"
        elif r["core_zero_seconds"] and not r["has_transaction_support"]:
            cls = "zero_seconds_no_transaction_support"
        elif r["core_zero_seconds"]:
            cls = "zero_seconds_with_transaction_support"
        elif not r["has_transaction_support"]:
            cls = "positive_seconds_no_transaction_support"
        else:
            cls = "positive_seconds_with_transaction_support"
        r["classification"] = cls
        class_counts[cls] += 1
        by_season[r["season"]][cls] += 1

    zero_unsupported_keys = {
        (r["season"], r["player_id"], r["team_id"])
        for r in rows if r["classification"] == "zero_seconds_no_transaction_support"
    }
    overlap_coverage = 0
    overlap_both_zero_unsupported = 0
    for o in overlaps:
        lk = (str(o["season"]), str(o["player_id"]), int(o["left_team_id"]))
        rk = (str(o["season"]), str(o["player_id"]), int(o["right_team_id"]))
        if lk in zero_unsupported_keys or rk in zero_unsupported_keys:
            overlap_coverage += 1
        if lk in zero_unsupported_keys and rk in zero_unsupported_keys:
            overlap_both_zero_unsupported += 1

    report = {
        "strict_cross_team_overlap_count": len(overlaps),
        "overlap_player_seasons": len(player_seasons),
        "overlap_team_affiliations": len(rows),
        "classification_counts": dict(class_counts),
        "overlaps_touching_zero_seconds_no_transaction_support": overlap_coverage,
        "overlaps_with_both_sides_zero_seconds_no_transaction_support": overlap_both_zero_unsupported,
        "by_season": {s: dict(c) for s, c in sorted(by_season.items())},
        "rows": rows,
        "purpose": "Test whether surviving cross-team overlaps are caused by core player-total rows with zero on-court seconds and no independent transaction evidence. No data is modified.",
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in {"rows", "by_season"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
