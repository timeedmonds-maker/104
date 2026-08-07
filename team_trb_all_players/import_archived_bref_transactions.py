from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import build_historical_transactions_archive_resumable as archive


BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "historical_transactions"
RAW = OUT / "raw_pages_basketball_reference"
ROWS = OUT / "season_rows"
SUMMARIES = OUT / "season_summaries"

SNAPSHOTS = {
    2001: "https://web.archive.org/web/20221209183617id_/https://www.basketball-reference.com/leagues/NBA_2002_transactions.html",
    2002: "https://web.archive.org/web/20210810125923id_/https://www.basketball-reference.com/leagues/NBA_2003_transactions.html",
    2003: "https://web.archive.org/web/20221209192231id_/https://www.basketball-reference.com/leagues/NBA_2004_transactions.html",
}

EXPECTED_COUNTS = {2001: 253, 2002: 238, 2003: 368}

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


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    ROWS.mkdir(parents=True, exist_ok=True)
    SUMMARIES.mkdir(parents=True, exist_ok=True)

    requested = {int(value) for value in sys.argv[1:]} if len(sys.argv) > 1 else set(SNAPSHOTS)
    unknown = requested - set(SNAPSHOTS)
    if unknown:
        raise RuntimeError(f"unsupported season start years: {sorted(unknown)}")

    for year, url in SNAPSHOTS.items():
        if year not in requested:
            continue

        season = season_label(year)
        print(f"IMPORT {season}", flush=True)

        response = requests.get(
            url,
            headers={"User-Agent": "TREB-historical-roster-research/2.0"},
            timeout=60,
        )
        response.raise_for_status()
        html = response.text

        raw_path = RAW / f"{season}.html.gz"
        with gzip.open(raw_path, "wt", encoding="utf-8") as f:
            f.write(html)

        soup = BeautifulSoup(html, "html.parser")
        page_title = soup.title.get_text(" ", strip=True) if soup.title else season

        groups = [
            li for li in soup.find_all("li")
            if li.find("span")
            and DATE_RE.match(li.find("span").get_text(" ", strip=True))
        ]

        rows = []
        row_index = 0

        for li in groups:
            date = li.find("span").get_text(" ", strip=True)

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
                        "subsection": date,
                        "table_index": None,
                        "table_classes": [],
                        "row_index": row_index,
                        "cells": [date, text],
                        "links": links,
                        "date_hint": date.rsplit(",", 1)[0],
                        "source": "Basketball-Reference via Internet Archive",
                        "source_url": url,
                    }
                )
                row_index += 1

        expected = EXPECTED_COUNTS[year]
        if len(rows) != expected:
            raise RuntimeError(
                f"{season}: expected {expected} transactions, parsed {len(rows)}"
            )

        rows_path = ROWS / f"{season}.jsonl.gz"
        with gzip.open(rows_path, "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        section_counts = Counter(row["section"] for row in rows)

        summary = {
            "season": season,
            "page_title": page_title,
            "source": "Basketball-Reference via Internet Archive",
            "archive_snapshot": url,
            "raw_page": str(raw_path),
            "season_rows_file": str(rows_path),
            "rows": len(rows),
            "tables": 0,
            "section_table_counts": dict(sorted(section_counts.items())),
            "completed_utc": datetime.now(timezone.utc).isoformat(),
        }

        (SUMMARIES / f"{season}.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(
            f"  COMPLETE {season}: {len(rows)} transactions "
            f"{dict(sorted(section_counts.items()))}",
            flush=True,
        )

    final = archive.persist_state(
        [],
        datetime.now(timezone.utc).isoformat(),
    )

    print(
        json.dumps(
            {
                "completed_seasons": final["completed_seasons"],
                "unresolved_seasons": final["unresolved_seasons"],
                "total_rows": final["total_rows"],
                "mutombo_2001_validated": final["mutombo_2001_validated"],
                "complete": final["complete"],
            },
            indent=2,
        )
    )

    return 0 if final["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
