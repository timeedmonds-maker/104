from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import enrich_transactions_realgm as base
import enrich_transactions_realgm_resilient_v2 as v2
import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
NORMALIZED = ROOT / "normalized_transactions.jsonl.gz"
CACHE = ROOT / "realgm_transactions_resilient"
SUMMARY = ROOT / "realgm_cache_replay_summary.json"
MIN_EVENTS = 20


def load_rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda e: (
        str(e.get("exact_date") or ""), str(e.get("player_id") or ""),
        str(e.get("event_type") or ""), str(e.get("source_reference") or ""),
    ))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    context = norm.load_core_context()
    existing = load_rows(NORMALIZED)
    existing_keys = {
        (
            str(e.get("exact_date") or ""), str(e.get("season") or ""),
            str(e.get("event_type") or ""), str(e.get("player_id") or ""),
            int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
        )
        for e in existing
    }

    accepted: dict[str, int] = {}
    rejected: dict[str, str] = {}
    added = 0
    if CACHE.exists():
        for payload_path in sorted(CACHE.glob("*.txt.gz")):
            season = payload_path.name.replace(".txt.gz", "")
            if not season[:4].isdigit():
                continue
            year = int(season[:4])
            try:
                with gzip.open(payload_path, "rt", encoding="utf-8") as handle:
                    payload = handle.read()
                parsed = v2.valid_parse(year, payload, context)
            except Exception as exc:
                rejected[season] = repr(exc)
                continue
            if len(parsed) < MIN_EVENTS:
                rejected[season] = f"parsed_only_{len(parsed)}"
                continue
            accepted[season] = len(parsed)
            for event in parsed:
                key = (
                    str(event.get("exact_date") or ""), str(event.get("season") or ""),
                    str(event.get("event_type") or ""), str(event.get("player_id") or ""),
                    int(event.get("source_team_id") or 0), int(event.get("destination_team_id") or 0),
                )
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                existing.append(event)
                added += 1

    write_rows(NORMALIZED, existing)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "validated_cached_seasons_replayed": len(accepted),
        "accepted_season_counts": accepted,
        "rejected_or_invalid_cached_seasons": rejected,
        "events_added": added,
        "network_requests": 0,
        "policy": "Only locally cached RealGM payloads that still parse at >=20 roster events are replayed; no network retry is performed.",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
