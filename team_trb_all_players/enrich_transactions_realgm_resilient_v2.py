from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import enrich_transactions_realgm as base
import enrich_transactions_realgm_resilient as r1
import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
NORMALIZED = ROOT / "normalized_transactions.jsonl.gz"
CACHE = ROOT / "realgm_transactions_resilient"
SUMMARY = ROOT / "realgm_enrichment_summary.json"
YEARS = list(range(2000, 2016))
MIN_EVENTS = 20


def valid_parse(year: int, payload: str, context: norm.CoreContext) -> list[dict[str, Any]]:
    return base.dedupe(r1.parse_payload(year, payload, context))


def jina_urls(url: str) -> list[str]:
    nested = url.split("://", 1)[1]
    return [
        "https://r.jina.ai/http://" + nested,
        "https://r.jina.ai/https://" + nested,
    ]


def fetch_wayback(session: requests.Session, url: str) -> tuple[str, str]:
    cdx = session.get(
        "https://web.archive.org/cdx/search/cdx",
        params={
            "url": url,
            "output": "json",
            "filter": "statuscode:200",
            "fl": "timestamp,original,statuscode,digest,length",
            "collapse": "digest",
        },
        timeout=(15, 60),
    )
    cdx.raise_for_status()
    payload = cdx.json()
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("no archived snapshots")
    header = payload[0]
    rows = [dict(zip(header, row)) for row in payload[1:] if len(row) == len(header)]
    candidates = [
        row for row in rows
        if str(row.get("timestamp", "")).isdigit()
        and str(row["timestamp"]) <= "20261231235959"
    ]
    if not candidates:
        raise RuntimeError("no usable archived snapshot")
    chosen = max(candidates, key=lambda row: str(row["timestamp"]))
    snapshot = f"https://web.archive.org/web/{chosen['timestamp']}id_/{url}"
    response = session.get(snapshot, headers=base.HEADERS, timeout=(15, 90))
    response.raise_for_status()
    if len(response.text) < 5000:
        raise RuntimeError(f"short archive payload ({len(response.text)} chars)")
    return response.text, f"wayback:{chosen['timestamp']}"


def acquire_valid_payload(
    year: int,
    session: requests.Session,
    context: norm.CoreContext,
) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    url = base.page_url(year)
    errors: list[str] = []

    try:
        payload = base.get(session, url)
        parsed = valid_parse(year, payload, context)
        if len(parsed) >= MIN_EVENTS:
            return payload, "direct", parsed, errors
        errors.append(f"direct parsed={len(parsed)}")
    except Exception as exc:
        errors.append(f"direct={exc!r}")

    for proxy_url in jina_urls(url):
        try:
            response = session.get(
                proxy_url,
                headers={"User-Agent": base.HEADERS["User-Agent"]},
                timeout=(10, 60),
            )
            response.raise_for_status()
            if len(response.text) < 5000:
                raise RuntimeError(f"short reader payload ({len(response.text)} chars)")
            parsed = valid_parse(year, response.text, context)
            if len(parsed) >= MIN_EVENTS:
                transport = "jina_http" if "r.jina.ai/http://" in proxy_url else "jina_https"
                return response.text, transport, parsed, errors
            errors.append(f"{proxy_url} parsed={len(parsed)}")
        except Exception as exc:
            errors.append(f"{proxy_url}={exc!r}")

    try:
        payload, transport = fetch_wayback(session, url)
        parsed = valid_parse(year, payload, context)
        if len(parsed) >= MIN_EVENTS:
            return payload, transport, parsed, errors
        errors.append(f"{transport} parsed={len(parsed)}")
    except Exception as exc:
        errors.append(f"wayback={exc!r}")

    raise RuntimeError(
        f"{base.season_label(year)}: no RealGM transport produced a valid parse; "
        + "; ".join(errors)
    )


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
    context = norm.load_core_context()
    session = requests.Session()
    CACHE.mkdir(parents=True, exist_ok=True)

    all_events: list[dict[str, Any]] = []
    season_counts: dict[str, int] = {}
    transports: dict[str, str] = {}
    refresh_notes: dict[str, list[str]] = {}

    for year in YEARS:
        season = base.season_label(year)
        payload_path = CACHE / f"{season}.txt.gz"
        meta_path = CACHE / f"{season}.json"
        parsed: list[dict[str, Any]] = []
        transport = ""
        source_state = "cache"

        if payload_path.exists() and meta_path.exists():
            with gzip.open(payload_path, "rt", encoding="utf-8") as handle:
                payload = handle.read()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            transport = str(meta.get("transport") or "cache")
            parsed = valid_parse(year, payload, context)
            if len(parsed) < MIN_EVENTS:
                refresh_notes[season] = [f"cached transport {transport} parsed only {len(parsed)} events"]
                parsed = []

        if not parsed:
            payload, transport, parsed, errors = acquire_valid_payload(year, session, context)
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
            }, indent=2), encoding="utf-8")
            source_state = "network"
            time.sleep(0.25)

        season_counts[season] = len(parsed)
        transports[season] = transport
        all_events.extend(parsed)
        print(f"REALGM {season}: {len(parsed)} roster events ({source_state}; {transport})", flush=True)

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
        "seasons": len(YEARS),
        "parsed_realgm_events": len(all_events),
        "added_events": len(added),
        "season_counts": season_counts,
        "transports": transports,
        "refresh_notes": refresh_notes,
        "source": "RealGM NBA league transaction history",
        "source_urls": [base.page_url(year) for year in YEARS],
        "purpose": (
            "exact historical waiver, free-agent, 10-day-expiry, signing and trade "
            "boundaries missing from Basketball-Reference transaction prose"
        ),
        "validation": "each season must parse at least 20 roster events before acceptance",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
