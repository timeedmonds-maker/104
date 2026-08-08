from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import enrich_transactions_realgm as base
import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
NORMALIZED = ROOT / "normalized_transactions.jsonl.gz"
CACHE = ROOT / "realgm_transactions_resilient"
SUMMARY = ROOT / "realgm_enrichment_summary.json"
YEARS = list(range(2000, 2016))

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
MD_DATE_RE = re.compile(r"^#{2,5}\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})(?:\b.*)?$")
MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")


def clean_markdown_text(text: str) -> str:
    text = MD_LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "")
    text = text.replace("\\-", "-").replace("\\.", ".")
    return re.sub(r"\s+", " ", text).strip()


def parse_markdown(year: int, text: str, context: norm.CoreContext) -> list[dict[str, Any]]:
    season = base.season_label(year)
    day: str | None = None
    events: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        dm = MD_DATE_RE.match(line)
        if dm:
            day = dm.group(1)
            continue
        if day is None:
            continue
        bm = MD_BULLET_RE.match(line)
        if not bm:
            continue
        item = clean_markdown_text(bm.group(1))
        if any(key in item.casefold() for key in (
            "became a free agent", "on waivers", "terminated the 10",
            "signed a contract", "signed a ", "signed with the ",
            "successful waiver claim", "was acquired by the "
        )):
            events.extend(base.parse_transaction(day, season, item, context))
    return events


def fetch_direct(session: requests.Session, url: str) -> tuple[str, str]:
    try:
        return base.get(session, url), "direct"
    except Exception as direct_exc:
        errors = [f"direct={direct_exc!r}"]

    # RealGM returns 403 to non-browser cloud clients. Jina Reader is used only
    # as a transport layer for the same public RealGM page; the source remains
    # RealGM and the exact source URL is preserved in every normalized event.
    for prefix in ("https://r.jina.ai/http://", "https://r.jina.ai/https://"):
        nested = url.split("://", 1)[1]
        proxy_url = prefix + nested
        try:
            r = session.get(proxy_url, headers={"User-Agent": base.HEADERS["User-Agent"]}, timeout=(10, 60))
            r.raise_for_status()
            if len(r.text) < 5000:
                raise RuntimeError(f"short reader payload ({len(r.text)} chars)")
            return r.text, "jina_reader"
        except Exception as exc:
            errors.append(f"{proxy_url}={exc!r}")

    # Final transport fallback: a stable Internet Archive copy of the same page.
    try:
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
        candidates = [r for r in rows if str(r.get("timestamp", "")).isdigit() and str(r["timestamp"]) <= "20261231235959"]
        if not candidates:
            raise RuntimeError("no usable archived snapshot")
        chosen = max(candidates, key=lambda r: str(r["timestamp"]))
        snapshot = f"https://web.archive.org/web/{chosen['timestamp']}id_/{url}"
        r = session.get(snapshot, headers=base.HEADERS, timeout=(15, 90))
        r.raise_for_status()
        if len(r.text) < 5000:
            raise RuntimeError(f"short archive payload ({len(r.text)} chars)")
        return r.text, f"wayback:{chosen['timestamp']}"
    except Exception as exc:
        errors.append(f"wayback={exc!r}")

    raise RuntimeError(f"All RealGM transports failed for {url}: " + "; ".join(errors))


def parse_payload(year: int, payload: str, context: norm.CoreContext) -> list[dict[str, Any]]:
    # HTML pages are parsed by the original validated parser. Reader output is
    # Markdown and is parsed line-by-line into the same transaction parser.
    if "<html" in payload[:5000].casefold() or "<!doctype" in payload[:5000].casefold():
        return base.parse_page(year, payload, context)
    return parse_markdown(year, payload, context)


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

    for year in YEARS:
        season = base.season_label(year)
        payload_path = CACHE / f"{season}.txt.gz"
        meta_path = CACHE / f"{season}.json"
        if payload_path.exists() and meta_path.exists():
            with gzip.open(payload_path, "rt", encoding="utf-8") as handle:
                payload = handle.read()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            transport = str(meta.get("transport") or "cache")
            source_state = "cache"
        else:
            payload, transport = fetch_direct(session, base.page_url(year))
            with gzip.open(payload_path, "wt", encoding="utf-8") as handle:
                handle.write(payload)
            meta_path.write_text(json.dumps({
                "season": season,
                "source_url": base.page_url(year),
                "transport": transport,
                "fetched_utc": datetime.now(timezone.utc).isoformat(),
                "chars": len(payload),
            }, indent=2), encoding="utf-8")
            source_state = "network"
            time.sleep(0.25)

        parsed = base.dedupe(parse_payload(year, payload, context))
        if len(parsed) < 20:
            raise RuntimeError(
                f"{season}: resilient RealGM parser produced only {len(parsed)} roster events "
                f"({source_state}, transport={transport})"
            )
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
    for e in all_events:
        key = (
            e["exact_date"], e["season"], e["event_type"], str(e["player_id"]),
            int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
        )
        if key not in existing_keys:
            existing_keys.add(key)
            existing.append(e)
            added.append(e)

    write_normalized(existing)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "seasons": len(YEARS),
        "parsed_realgm_events": len(all_events),
        "added_events": len(added),
        "season_counts": season_counts,
        "transports": transports,
        "source": "RealGM NBA league transaction history",
        "source_urls": [base.page_url(year) for year in YEARS],
        "purpose": (
            "exact historical waiver, free-agent, 10-day-expiry, signing and trade "
            "boundaries missing from Basketball-Reference transaction prose"
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
