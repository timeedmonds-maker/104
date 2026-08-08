from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
AUDIT = ROOT / "tenure_consistency_audit.json"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
WINDOWS = ROOT / "player_team_season_windows_evidence_audited.jsonl.gz"
OUT = ROOT / "remaining_overlap_event_chains_v12.json"


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    overlaps = audit.get("strict_cross_team_overlaps") or []
    events = read_jsonl_gz(EVENTS)
    windows = read_jsonl_gz(WINDOWS)
    keys = sorted({(str(o.get("season") or ""), str(o.get("player_id") or "")) for o in overlaps})
    by_event: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_window: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_overlap: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        key = (str(e.get("season") or ""), str(e.get("player_id") or ""))
        if key in keys: by_event[key].append(e)
    for w in windows:
        key = (str(w.get("season") or ""), str(w.get("player_id") or ""))
        if key in keys: by_window[key].append(w)
    for o in overlaps:
        by_overlap[(str(o.get("season") or ""), str(o.get("player_id") or ""))].append(o)
    cases = []
    for key in keys:
        season, pid = key
        ev = sorted(by_event.get(key, []), key=lambda x: (str(x.get("exact_date") or ""), str(x.get("source_reference") or "")))
        win = sorted(by_window.get(key, []), key=lambda x: (str(x.get("query_start_date") or x.get("tenure_start") or ""), int(x.get("team_id") or 0)))
        cases.append({
            "season": season, "player_id": pid,
            "player_name": by_overlap[key][0].get("player_name") if by_overlap.get(key) else None,
            "overlaps": by_overlap.get(key, []),
            "windows": [{k: w.get(k) for k in (
                "team_id", "team_abbr", "tenure_start", "tenure_end", "query_start_date", "query_end_date",
                "start_reason", "end_reason", "start_source", "end_source", "confidence", "audit_flags"
            )} for w in win],
            "events": [{k: e.get(k) for k in (
                "exact_date", "event_type", "source_team_id", "destination_team_id", "source_team_name", "destination_team_name",
                "source_system", "source_reference", "raw_text", "confidence", "identity_resolution", "team_resolution",
                "derived_boundary_type", "derived_from_signing_date", "verified_boundary_v12", "v12_bubble_season_reassigned"
            )} for e in ev],
        })
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "strict_cross_team_overlap_count": len(overlaps),
        "player_season_case_count": len(keys),
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"strict_cross_team_overlap_count": len(overlaps), "player_season_case_count": len(keys), "output": str(OUT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
