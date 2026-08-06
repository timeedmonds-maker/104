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
except ImportError as exc:  # pragma: no cover - explicit operator guidance
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
    "User-Agent": "TREB-historical-roster-research/1.0 (GitHub: timeedmonds-maker/104)",
    "Accept": "application/json",
}

START_YEARS = range(2000, 2016)
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


def get_json(params: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(3):
        try:
            response = requests.get(
                API,
                params=params,
                headers=HEADERS,
                timeout=(10, 45),
            )
            print(
                f"GET {params.get('page') or params.get('titles')} "
                f"status={response.status_code} bytes={len(response.content)}",
                flush=True,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            errors.append(repr(exc))
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError("; ".join(errors))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def date_hint(cells: list[str], subsection: str | None) -> str | None:
    candidates = []
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
    current_section: str | None = None
    current_subsection: str | None = None
    table_counter = 0
    section_counts: dict[str, int] = {}

    for tag in soup.find_all(["h2", "h3", "h4", "table"]):
        if tag.name == "h2":
            heading = tag.find(class_="mw-headline")
            current_section = clean_text(heading.get_text(" ") if heading else tag.get_text(" "))
            current_subsection = None
            continue
        if tag.name in {"h3", "h4"}:
            heading = tag.find(class_="mw-headline")
            current_subsection = clean_text(
                heading.get_text(" ") if heading else tag.get_text(" ")
            )
            continue
        if tag.name != "table":
            continue

        table_counter += 1
        section = current_section or "Unsectioned"
        section_counts[section] = section_counts.get(section, 0) + 1
        table_classes = [str(value) for value in tag.get("class", [])]

        for row_index, tr in enumerate(tag.find_all("tr", recursive=False)):
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
                    "subsection": current_subsection,
                    "table_index": table_counter,
                    "table_classes": table_classes,
                    "row_index": row_index,
                    "cells": cells,
                    "links": links,
                    "date_hint": date_hint(cells, current_subsection),
                }
            )

    return rows, {
        "season": season_label(start_year),
        "page_title": page_title(start_year),
        "html_bytes": len(raw_html.encode("utf-8")),
        "tables": table_counter,
        "rows": len(rows),
        "section_table_counts": section_counts,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for start_year in START_YEARS:
        title = page_title(start_year)
        try:
            payload = get_json(
                {
                    "action": "parse",
                    "page": title,
                    "prop": "text|sections",
                    "format": "json",
                    "formatversion": "2",
                    "redirects": "1",
                }
            )
            parse = payload.get("parse", {})
            raw_html = str(parse.get("text") or "")
            if not raw_html:
                raise RuntimeError("MediaWiki returned empty page HTML")

            raw_path = RAW_DIR / f"{season_label(start_year)}.html.gz"
            with gzip.open(raw_path, "wt", encoding="utf-8") as handle:
                handle.write(raw_html)

            rows, summary = parse_page(start_year, raw_html)
            summary["raw_page"] = str(raw_path)
            summary["api_sections"] = [
                clean_text(str(item.get("line") or ""))
                for item in parse.get("sections", [])
            ]
            all_rows.extend(rows)
            page_summaries.append(summary)
            print(
                f"ARCHIVED {summary['season']} tables={summary['tables']} rows={summary['rows']}",
                flush=True,
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
        "complete": not failures and len(page_summaries) == len(list(START_YEARS)),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "complete": summary["complete"],
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
    return 0 if summary["complete"] and mutombo_matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
