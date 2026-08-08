from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "historical_transactions" / "basketball_reference_uniform"
RAW = OUT / "raw_pages"
ROWS = OUT / "season_rows"
SUMMARIES = OUT / "season_summaries"
MANIFEST = OUT / "manifest.json"

YEARS = list(range(2000, 2016))
USER_AGENT = "TREB-historical-roster-research/3.0"
CDX_URL = "https://web.archive.org/cdx/search/cdx"

def get_with_retry(session, url, *, attempts=6, **kwargs):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            r = session.get(url, **kwargs)
            if r.status_code not in {429, 500, 502, 503, 504}:
                r.raise_for_status()
                return r
            last = RuntimeError(f"HTTP {r.status_code} from {r.url}")
        except requests.RequestException as exc:
            last = exc
        if attempt < attempts:
            delay = min(60, 5 * (2 ** (attempt - 1)))
            print(f"Transient archive error; retry {attempt}/{attempts} after {delay}s: {last}", flush=True)
            time.sleep(delay)
    raise last

# Already inspected and validated in the TREB build. Keep these fixed so the
# two missing Wikipedia seasons and the empty 2003-04 Wikipedia page remain
# reproducible even if the Wayback index changes later.
PINNED_SNAPSHOTS = {
    2001: "20221209183617",
    2002: "20210810125923",
    2003: "20221209192231",
}
PINNED_COUNTS = {2001: 253, 2002: 238, 2003: 368}

DATE_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December) "
    r"\d{1,2}, \d{4}$"
)


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def classify(text: str) -> str:
    t = text.casefold()
    if " traded " in f" {t} ":
        return "Trades"
    if " signed " in f" {t} ":
        return "Signings"
    if " waived " in f" {t} ":
        return "Waived"
    if " released " in f" {t} ":
        return "Released"
    if " claimed " in f" {t} ":
        return "Claimed"
    if " activated " in f" {t} ":
        return "Activated"
    if "injured list" in t:
        return "Injured list"
    return "Other"


def archive_target(year: int) -> str:
    return f"https://www.basketball-reference.com/leagues/NBA_{year + 1}_transactions.html"


def choose_snapshot(year: int, session: requests.Session) -> tuple[str, dict[str, Any]]:
    if year in PINNED_SNAPSHOTS:
        timestamp = PINNED_SNAPSHOTS[year]
        return timestamp, {"method": "pinned", "timestamp": timestamp}

    target = archive_target(year)
    response = get_with_retry(
        session,
        CDX_URL,
        params={
            "url": target,
            "output": "json",
            "filter": "statuscode:200",
            "fl": "timestamp,original,statuscode,digest,length",
            "collapse": "digest",
        },
        timeout=(15, 60),
    )
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"{season_label(year)}: no Wayback snapshots returned")

    header = payload[0]
    records = [dict(zip(header, row)) for row in payload[1:] if len(row) == len(header)]
    candidates = [
        row for row in records
        if str(row.get("timestamp", "")).isdigit()
        and str(row.get("timestamp")) <= "20241231235959"
    ]
    if not candidates:
        raise RuntimeError(f"{season_label(year)}: no usable Wayback snapshot before 2025")

    # Prefer a mature 2022 snapshot when possible: old enough to be stable,
    # recent enough to contain Basketball-Reference's corrected transaction log.
    before_2023 = [row for row in candidates if str(row["timestamp"]) <= "20221231235959"]
    chosen = max(before_2023 or candidates, key=lambda row: str(row["timestamp"]))
    return str(chosen["timestamp"]), {
        "method": "wayback_cdx_latest_before_2023" if before_2023 else "wayback_cdx_latest_before_2025",
        "timestamp": str(chosen["timestamp"]),
        "cdx_record": chosen,
        "candidate_count": len(candidates),
    }


def snapshot_url(year: int, timestamp: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{archive_target(year)}"


def parse_page(year: int, html: str, source_url: str) -> list[dict[str, Any]]:
    season = season_label(year)
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else season
    groups = [
        li for li in soup.find_all("li")
        if li.find("span")
        and DATE_RE.match(li.find("span").get_text(" ", strip=True))
    ]

    rows: list[dict[str, Any]] = []
    row_index = 0
    for li in groups:
        transaction_date = li.find("span").get_text(" ", strip=True)
        for p in li.find_all("p", recursive=False):
            text = p.get_text(" ", strip=True)
            if not text:
                continue
            links = [
                {
                    "text": a.get_text(" ", strip=True),
                    "title": a.get("title"),
                    "href": a.get("href"),
                }
                for a in p.find_all("a")
            ]
            rows.append(
                {
                    "season": season,
                    "start_year": year,
                    "page_title": page_title,
                    "section": classify(text),
                    "subsection": transaction_date,
                    "table_index": None,
                    "table_classes": [],
                    "row_index": row_index,
                    "cells": [transaction_date, text],
                    "links": links,
                    "date_hint": transaction_date.rsplit(",", 1)[0],
                    "exact_date": transaction_date,
                    "source": "Basketball-Reference via Internet Archive",
                    "source_url": source_url,
                }
            )
            row_index += 1
    return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate(year: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    season = season_label(year)
    if not rows:
        raise RuntimeError(f"{season}: zero parsed transactions")
    if not all(DATE_RE.match(str(row.get("exact_date", ""))) for row in rows):
        raise RuntimeError(f"{season}: at least one transaction lacks an exact source date")
    if year in PINNED_COUNTS and len(rows) != PINNED_COUNTS[year]:
        raise RuntimeError(
            f"{season}: expected {PINNED_COUNTS[year]} transactions, parsed {len(rows)}"
        )

    searchable = "\n".join(" ".join(map(str, row.get("cells", []))) for row in rows)
    if year == 2000 and not (
        "Dikembe Mutombo" in searchable
        and "Philadelphia 76ers" in searchable
        and "Atlanta Hawks" in searchable
        and "February 22, 2001" in searchable
    ):
        raise RuntimeError("2000-01: Mutombo Atlanta-to-Philadelphia trade not found")
    if year == 2003:
        checks = (
            ("December 1, 2003", "Jalen Rose", "Toronto Raptors"),
            ("February 19, 2004", "Rasheed Wallace", "Detroit Pistons"),
            ("June 29, 2004", "Tracy McGrady", "Houston Rockets"),
        )
        for transaction_date, player, team in checks:
            if not any(
                row.get("exact_date") == transaction_date
                and player in " ".join(map(str, row.get("cells", [])))
                and team in " ".join(map(str, row.get("cells", [])))
                for row in rows
            ):
                raise RuntimeError(f"2003-04: QA transaction missing: {transaction_date} {player}")

    sections = Counter(str(row.get("section") or "") for row in rows)
    return {
        "season": season,
        "rows": len(rows),
        "section_counts": dict(sorted(sections.items())),
        "exact_dates": len(rows),
        "trade_rows": sections.get("Trades", 0),
    }


def import_year(year: int, session: requests.Session, *, force: bool = False) -> dict[str, Any]:
    season = season_label(year)
    raw_path = RAW / f"{season}.html.gz"
    rows_path = ROWS / f"{season}.jsonl.gz"
    summary_path = SUMMARIES / f"{season}.json"

    if not force and raw_path.exists() and rows_path.exists() and summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("validated") is True:
            print(f"CACHE {season}: {summary.get('rows')} transactions", flush=True)
            return summary

    timestamp, snapshot = choose_snapshot(year, session)
    url = snapshot_url(year, timestamp)
    print(f"FETCH {season}: {url}", flush=True)
    response = get_with_retry(session, url, timeout=(15, 90))
    html = response.text
    if len(html) < 5000:
        raise RuntimeError(f"{season}: suspiciously short archived page ({len(html)} chars)")

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(raw_path, "wt", encoding="utf-8") as handle:
        handle.write(html)

    rows = parse_page(year, html, url)
    qa = validate(year, rows)
    write_rows(rows_path, rows)
    summary = {
        **qa,
        "validated": True,
        "snapshot": snapshot,
        "source_url": url,
        "raw_path": str(raw_path),
        "rows_path": str(rows_path),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"COMPLETE {season}: {qa['rows']} transactions, {qa['trade_rows']} trades",
        flush=True,
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("years", nargs="*", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    years = args.years or YEARS
    invalid = sorted(set(years) - set(YEARS))
    if invalid:
        raise SystemExit(f"Unsupported start years: {invalid}")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/json"})

    summaries = []
    for index, year in enumerate(years, start=1):
        print(f"[{index}/{len(years)}] {season_label(year)}", flush=True)
        summaries.append(import_year(year, session, force=args.force))
        time.sleep(0.5)

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "requested_start_years": years,
        "completed_seasons": [summary["season"] for summary in summaries],
        "all_validated": all(summary.get("validated") is True for summary in summaries),
        "total_rows": sum(int(summary.get("rows", 0)) for summary in summaries),
        "total_trade_rows": sum(int(summary.get("trade_rows", 0)) for summary in summaries),
        "summaries": summaries,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    return 0 if manifest["all_validated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
