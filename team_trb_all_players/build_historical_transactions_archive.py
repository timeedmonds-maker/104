from __future__ import annotations

import gzip
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

try:
    from bs4 import BeautifulSoup
except ImportError as exc:
    raise SystemExit(
        "Missing dependency beautifulsoup4. Run: python -m pip install beautifulsoup4"
    ) from exc

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "impact_database" / "historical_transactions"
RAW_DIR = OUT_DIR / "raw_pages"
ROWS_PATH = OUT_DIR / "wikipedia_transaction_rows.jsonl.gz"
SUMMARY_PATH = OUT_DIR / "archive_summary.json"

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": (
        "TREB-historical-roster-research/1.1 "
        "(https://github.com/timeedmonds-maker/104)"
    ),
    "Accept": "application/json",
}

START_YEARS = range(2000, 2016)
REMOTE_REQUEST_PAUSE_SECONDS = 10
MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}
DATE_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})\b",
    flags=re.I,
)


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def wikipedia_label(start_year: int) -> str:
    return f"{start_year}\u2013{str(start_year + 1)[-2:]}"


def page_title(start_year: int) -> str:
    return f"List of {wikipedia_label(start_year)} NBA season transactions"


def wait_with_progress(seconds: int, reason: str) -> None:
    remaining = max(0, int(seconds))
    while remaining > 0:
        chunk = min(10, remaining)
        print(f"WAIT {remaining}s: {reason}", flush=True)
        time.sleep(chunk)
        remaining -= chunk


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def heading_text(tag: Any) -> str:
    heading = tag.find(class_="mw-headline")
    return clean_text(heading.get_text(" ") if heading else tag.get_text(" "))


def table_context(table: Any) -> tuple[str, str | None]:
    section: str | None = None
    subsection: str | None = None
    for previous in table.find_all_previous(["h2", "h3", "h4"]):
        if previous.name in {"h3", "h4"} and subsection is None:
            subsection = heading_text(previous)
            continue
        if previous.name == "h2":
            section = heading_text(previous)
            break
    return section or "Unsectioned", subsection


def date_hint(cells: list[str], subsection: str | None) -> str | None:
    candidates: list[str] = []
    if subsection:
        candidates.append(subsection)
    candidates.extend(cells[:2])
    for candidate in candidates:
        match = DATE_RE.search(candidate)
        if match:
            month = match.group(1).title()
            day = int(match.group(2))
            return f"{month} {day}"
    return None


def parse_page(start_year: int, raw_html: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    soup = BeautifulSoup(raw_html, "html.parser")
    rows: list[dict[str, Any]] = []
    section_counts: dict[str, int] = {}
    tables = soup.find_all("table")

    for table_index, table in enumerate(tables, start=1):
        section, subsection = table_context(table)
        section_counts[section] = section_counts.get(section, 0) + 1
        table_classes = [str(value) for value in table.get("class", [])]

        for row_index, tr in enumerate(table.find_all("tr")):
            # Ignore rows belonging to a nested table rather than this table.
            if tr.find_parent("table") is not table:
                continue
            cells = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in tr.find_all(["th", "td"], recursive=False)
            ]
            if not cells or not any(cells):
                continue

            links = []
            for anchor in tr.find_all("a"):
                text = clean_text(anchor.get_text(" ", strip=True))
                title = clean_text(str(anchor.get("title") or ""))
                href = str(anchor.get("href") or "")
                if text or title:
                    links.append({"text": text, "title": title, "href": href})

            rows.append(
                {
                    "season": season_label(start_year),
                    "start_year": start_year,
                    "page_title": page_title(start_year),
                    "section": section,
                    "subsection": subsection,
                    "table_index": table_index,
                    "table_classes": table_classes,
                    "row_index": row_index,
                    "cells": cells,
                    "links": links,
                    "date_hint": date_hint(cells, subsection),
                }
            )

    return rows, {
        "season": season_label(start_year),
        "page_title": page_title(start_year),
        "html_bytes": len(raw_html.encode("utf-8")),
        "tables": len(tables),
        "rows": len(rows),
        "section_table_counts": section_counts,
    }


def fetch_page(
    session: requests.Session,
    start_year: int,
    raw_path: Path,
) -> tuple[str, str]:
    if raw_path.exists() and raw_path.stat().st_size > 100:
        with gzip.open(raw_path, "rt", encoding="utf-8") as handle:
            raw_html = handle.read()
        if raw_html.strip():
            print(f"CACHE {season_label(start_year)} bytes={len(raw_html.encode('utf-8'))}", flush=True)
            return raw_html, "cache"

    title = page_title(start_year)
    params = {
        "action": "parse",
        "page": title,
        "prop": "text|sections",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "maxlag": "5",
    }
    errors: list[str] = []

    for attempt in range(1, 9):
        try:
            response = session.get(
                API,
                params=params,
                headers=HEADERS,
                timeout=(10, 60),
            )
            print(
                f"GET {season_label(start_year)} attempt={attempt}/8 "
                f"status={response.status_code} bytes={len(response.content)}",
                flush=True,
            )

            if response.status_code in {429, 503}:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_seconds = int(float(retry_after)) if retry_after else 15 * (2 ** (attempt - 1))
                except ValueError:
                    wait_seconds = 15 * (2 ** (attempt - 1))
                wait_seconds = min(max(wait_seconds, 30), 300)
                wait_with_progress(wait_seconds, f"Wikipedia rate limit for {season_label(start_year)}")
                continue

            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(f"MediaWiki error: {payload['error']}")
            parse = payload.get("parse", {})
            raw_html = str(parse.get("text") or "")
            if not raw_html:
                raise RuntimeError("MediaWiki returned empty page HTML")

            with gzip.open(raw_path, "wt", encoding="utf-8") as handle:
                handle.write(raw_html)
            return raw_html, "remote"
        except Exception as exc:
            errors.append(repr(exc))
            if attempt < 8:
                wait_seconds = min(10 * (2 ** (attempt - 1)), 120)
                wait_with_progress(wait_seconds, f"request retry for {season_label(start_year)}")

    raise RuntimeError("; ".join(errors))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    all_rows: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for start_year in START_YEARS:
        raw_path = RAW_DIR / f"{season_label(start_year)}.html.gz"
        try:
            raw_html, source = fetch_page(session, start_year, raw_path)
            rows, summary = parse_page(start_year, raw_html)
            if not rows:
                raise RuntimeError("page was downloaded but no table rows were parsed")

            summary["raw_page"] = str(raw_path)
            summary["source"] = source
            all_rows.extend(rows)
            page_summaries.append(summary)
            print(
                f"ARCHIVED {summary['season']} source={source} "
                f"tables={summary['tables']} rows={summary['rows']}",
                flush=True,
            )
            if source == "remote":
                wait_with_progress(
                    REMOTE_REQUEST_PAUSE_SECONDS,
                    "polite pause between Wikipedia requests",
                )
        except Exception as exc:
            failures.append({"season": season_label(start_year), "error": repr(exc)})
            print(f"FAILED {season_label(start_year)}: {exc!r}", file=sys.stderr, flush=True)

    with gzip.open(ROWS_PATH, "wt", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    section_counts: dict[str, int] = {}
    for row in all_rows:
        section = str(row["section"])
        section_counts[section] = section_counts.get(section, 0) + 1

    mutombo_matches = [
        row
        for row in all_rows
        if row["season"] == "2000-01"
        and any("Dikembe Mutombo" in cell for cell in row["cells"])
    ]
    complete = (
        not failures
        and len(page_summaries) == len(list(START_YEARS))
        and bool(all_rows)
        and bool(mutombo_matches)
    )
    summary = {
        "source": API,
        "requested_seasons": len(list(START_YEARS)),
        "completed_seasons": len(page_summaries),
        "failed_seasons": failures,
        "total_rows": len(all_rows),
        "section_row_counts": section_counts,
        "mutombo_2001_rows": mutombo_matches,
        "page_summaries": page_summaries,
        "rows_file": str(ROWS_PATH),
        "complete": complete,
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "complete": complete,
                "completed_seasons": summary["completed_seasons"],
                "failed_seasons": failures,
                "total_rows": summary["total_rows"],
                "section_row_counts": section_counts,
                "mutombo_2001_row_count": len(mutombo_matches),
                "rows_file": str(ROWS_PATH),
                "summary_file": str(SUMMARY_PATH),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
