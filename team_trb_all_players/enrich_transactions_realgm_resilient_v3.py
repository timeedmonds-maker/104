from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import enrich_transactions_realgm as base
import enrich_transactions_realgm_resilient_v2 as v2
import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
NORMALIZED = ROOT / "normalized_transactions.jsonl.gz"
CACHE = ROOT / "realgm_transactions_resilient"
SUMMARY = ROOT / "realgm_enrichment_summary.json"
YEARS = list(range(2000, 2016))
MIN_EVENTS = 20


def load_normalized() -> list[dict[str, Any]]:
    with gzip.open(NORMALIZED, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_normalized(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda e: (
        str(e.get("exact_date") or ""), str(e.get("player_id") or ""),
        str(e.get("event_type") or ""), str(e.get("source_reference") or ""),
    ))
    with gzip.open(NORMALIZED, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    """Enrich every RealGM season that can be validated without blocking Stage 1.

    RealGM is a supplementary exact-boundary source, not the primary Stage-1
    source. A transport failure for one season must never discard already
    validated RealGM seasons or prevent the strict downstream overlap QA from
    measuring what remains. Every accepted season still has to parse at least
    MIN_EVENTS roster events; unavailable seasons are explicitly recorded.
    """
    context = norm.load_core_context()
    session = requests.Session()
    CACHE.mkdir(parents=True, exist_ok=True)

    all_events: list[dict[str, Any]] = []
    season_counts: dict[str, int] = {}
    transports: dict[str, str] = {}
    refresh_notes: dict[str, list[str]] = {}
    unavailable: dict[str, str] = {}

    for year in YEARS:
        season = base.season_label(year)
        payload_path = CACHE / f"{season}.txt.gz"
        meta_path = CACHE / f"{season}.json"
        parsed: list[dict[str, Any]] = []
        transport = ""
        source_state = "cache"

        if payload_path.exists() and meta_path.exists():
            try:
                with gzip.open(payload_path, "rt", encoding="utf-8") as handle:
                    payload = handle.read()
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                transport = str(meta.get("transport") or "cache")
                parsed = v2.valid_parse(year, payload, context)
                if len(parsed) < MIN_EVENTS:
                    refresh_notes[season] = [
                        f"cached transport {transport} parsed only {len(parsed)} events"
                    ]
                    parsed = []
            except Exception as exc:
                refresh_notes[season] = [f"cache validation failed: {exc!r}"]
                parsed = []

        if not parsed:
            try:
                payload, transport, parsed, errors = v2.acquire_valid_payload(year, session, context)
                refresh_notes.setdefault(season, []).extend(errors)
                with gzip.open(payload_path, "wt", encoding="utf-8") as handle:
                    handle.write(payload)
                meta_path.write_text(json.dumps({
                    "season": season,
                    "source_url": base.page_url(year),
                    "transport": transport,
                    "fetched_utc": datetime.now(timezone.utc).isoformat(),
                    "chars": len(payload),
                    "parsed_events": len(parsed),
                    "validated": True,
                }, indent=2), encoding="utf-8")
                source_state = "network"
                time.sleep(0.25)
            except Exception as exc:
                unavailable[season] = str(exc)
                print(f"REALGM {season}: unavailable after validated transport attempts; continuing", flush=True)
                continue

        season_counts[season] = len(parsed)
        transports[season] = transport
        all_events.extend(parsed)
        print(f"REALGM {season}: {len(parsed)} roster events ({source_state}; {transport})", flush=True)

    if not all_events:
        raise RuntimeError("RealGM enrichment produced zero validated events across all seasons")

    existing = load_normalized()
    existing_keys = {
        (
            str(e.get("exact_date") or ""), str(e.get("season") or ""),
            str(e.get("event_type") or ""), str(e.get("player_id") or ""),
            int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
        )
        for e in existing
    }
    added: list[dict[str, Any]] = []
    for event in all_events:
        key = (
            event["exact_date"], event["season"], event["event_type"], str(event["player_id"]),
            int(event.get("source_team_id") or 0), int(event.get("destination_team_id") or 0),
        )
        if key not in existing_keys:
            existing_keys.add(key)
            existing.append(event)
            added.append(event)

    write_normalized(existing)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "requested_seasons": len(YEARS),
        "validated_seasons": len(season_counts),
        "unavailable_seasons": sorted(unavailable),
        "unavailable_details": unavailable,
        "parsed_realgm_events": len(all_events),
        "added_events": len(added),
        "season_counts": season_counts,
        "transports": transports,
        "refresh_notes": refresh_notes,
        "source": "RealGM NBA league transaction history",
        "purpose": (
            "supplement exact historical waiver, free-agent, 10-day-expiry, signing and trade "
            "boundaries missing from Basketball-Reference transaction prose"
        ),
        "validation": (
            "each accepted season must parse at least 20 roster events; an unavailable season is "
            "recorded but does not discard accepted seasons or bypass downstream zero-overlap QA"
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
