from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
AUDIT = ROOT / "tenure_consistency_audit.json"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
WINDOWS = ROOT / "player_team_season_windows_evidence_audited.jsonl.gz"
OFFICIAL_RAW = ROOT / "official_movement_feed.json.gz"
OUT = ROOT / "remaining_overlap_root_cause.json"


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    overlaps = list(audit.get("strict_cross_team_overlaps") or [])
    keys = sorted({(str(x.get("season")), str(x.get("player_id"))) for x in overlaps})
    keyset = set(keys)

    events = [e for e in read_jsonl_gz(EVENTS) if (str(e.get("season")), str(e.get("player_id"))) in keyset]
    windows = [w for w in read_jsonl_gz(WINDOWS) if (str(w.get("season")), str(w.get("player_id"))) in keyset]

    raw_by_pid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if OFFICIAL_RAW.exists():
        with gzip.open(OFFICIAL_RAW, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        for row in norm.movement_rows(payload):
            pid = norm.clean_id(norm.ci(row, "PLAYER_ID", "PlayerId"))
            if pid and any(pid == k[1] for k in keys):
                raw_by_pid[pid].append({
                    "date": norm.ci(row, "TRANSACTION_DATE", "Date"),
                    "type": norm.ci(row, "Transaction_Type", "TRANSACTION_TYPE"),
                    "team_id": norm.ci(row, "TEAM_ID", "TeamId"),
                    "additional_sort": norm.ci(row, "Additional_Sort", "ADDITIONAL_SORT"),
                    "description": norm.ci(row, "TRANSACTION_DESCRIPTION", "Description"),
                })

    by_key_events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_key_events[(str(e.get("season")), str(e.get("player_id")))].append({
            "date": e.get("exact_date"),
            "type": e.get("event_type"),
            "src": e.get("source_team_id"),
            "dst": e.get("destination_team_id"),
            "source": e.get("source_system"),
            "raw": e.get("raw_text"),
            "derived": e.get("derived_boundary_type"),
        })

    by_key_windows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for w in windows:
        by_key_windows[(str(w.get("season")), str(w.get("player_id")))].append({
            "team": w.get("team_id"),
            "start": w.get("tenure_start"),
            "end": w.get("tenure_end"),
            "query_start": w.get("query_start_date"),
            "query_end": w.get("query_end_date"),
            "start_reason": w.get("start_reason"),
            "end_reason": w.get("end_reason"),
            "confidence": w.get("confidence"),
            "flags": w.get("audit_flags"),
        })

    groups = []
    for season, pid in keys:
        ov = [x for x in overlaps if str(x.get("season")) == season and str(x.get("player_id")) == pid]
        name = next((x.get("player_name") for x in ov if x.get("player_name")), pid)
        groups.append({
            "season": season,
            "player_id": pid,
            "player_name": name,
            "overlap_count": len(ov),
            "overlaps": ov,
            "windows": sorted(by_key_windows[(season, pid)], key=lambda x: (str(x.get("start")), int(x.get("team") or 0))),
            "events": sorted(by_key_events[(season, pid)], key=lambda x: (str(x.get("date")), str(x.get("type")), int(x.get("src") or 0), int(x.get("dst") or 0))),
            "official_raw_rows": raw_by_pid.get(pid, []),
        })

    summary = {
        "strict_cross_team_overlap_count": len(overlaps),
        "player_season_groups": len(groups),
        "groups": groups,
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"overlaps": len(overlaps), "groups": len(groups), "output": str(OUT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
