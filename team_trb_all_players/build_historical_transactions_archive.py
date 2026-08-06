from __future__ import annotations

import gzip
import html
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
PAGE_ROWS_DIR = OUT_DIR / "season_rows"
PAGE_SUMMARY_DIR = OUT_DIR / "season_summaries"
ROWS_PATH = OUT_DIR / "wikipedia_transaction_rows.jsonl.gz"
SUMMARY_PATH = OUT_DIR / "archive_summary.json"
FAILURES_PATH = OUT_DIR / "archive_failures.json"

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": (
        "TREB-historical-roster-research/2.0 "
        "(https://github.com/timeedmonds-maker/104; historical NBA roster research)"
    ),
    "Accept": "application/json",
}

START_YEARS = tuple(range(2000, 2016))
REMOTE_REQUEST_PAUSE_SECONDS = 20
MAX_ATTEMPTS = 10
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_replace(temp_path: Path, destination: Path) -> None:
    temp_path.replace(destination)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    atomic_replace(temp_path, path)


def atomic_write_json_gz(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    atomic_replace(temp_path, path)


def atomic_write_text_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
        handle.write(text)
    atomic_replace(temp_path, path)


def atomic_write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    atomic_replace(temp_path, path)


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def wait_with_progress(seconds: float, reason: str) -> None:
    remaining = max(0, int(round(seconds)))
    while remaining > 0:
        chunk = min(10, remaining)
        print(f"WAIT {remaining}s: {reason}", flush=True)
        time.sleep(chunk)
        remaining -= chunk


def retry_after_seconds(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_time = parsedate_to_datetime(value)
            if retry_time.tzinfo is None:
                retry_time = retry_time.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_time - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            return None


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


def cache_paths(start_year: int) -> dict[str, Path]:
    label = season_label(start_year)
    return {
        "payload": RAW_DIR / f"{label}.api.json.gz",
        "html": RAW_DIR / f"{label}.html.gz",
        "rows": PAGE_ROWS_DIR / f"{label}.rows.jsonl.gz",
        "summary": PAGE_SUMMARY_DIR / f"{label}.summary.json",
    }


def load_cached_rows(start_year: int) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    paths = cache_paths(start_year)
    if not paths["rows"].exists() or paths["rows"].stat().st_size <= 20:
        return None
    try:
        rows = read_jsonl_gz(paths["rows"])
        if not rows:
            return None
        summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
        if int(summary.get("rows", 0)) != len(rows):
            return None
        return rows, summary
    except Exception as exc:
        print(f"CACHE INVALID {season_label(start_year)}: {exc!r}", flush=True)
        return None


def fetch_page(
    session: requests.Session,
    start_year: int,
) -> tuple[str, str]:
    paths = cache_paths(start_year)

    if paths["payload"].exists() and paths["payload"].stat().st_size > 100:
        try:
            payload = read_json_gz(paths["payload"])
            raw_html = str(payload.get("parse", {}).get("text") or "")
            if raw_html.strip():
                if not paths["html"].exists():
                    atomic_write_text_gz(paths["html"], raw_html)
                print(
                    f"CACHE PAYLOAD {season_label(start_year)} "
                    f"bytes={len(raw_html.encode('utf-8'))}",
                    flush=True,
                )
                return raw_html, "cached_api_payload"
        except Exception as exc:
            print(f"PAYLOAD CACHE INVALID {season_label(start_year)}: {exc!r}", flush=True)

    if paths["html"].exists() and paths["html"].stat().st_size > 100:
        try:
            with gzip.open(paths["html"], "rt", encoding="utf-8") as handle:
                raw_html = handle.read()
            if raw_html.strip():
                print(
                    f"CACHE HTML {season_label(start_year)} "
                    f"bytes={len(raw_html.encode('utf-8'))}",
                    flush=True,
                )
                return raw_html, "cached_html"
        except Exception as exc:
            print(f"HTML CACHE INVALID {season_label(start_year)}: {exc!r}", flush=True)

    params = {
        "action": "parse",
        "page": page_title(start_year),
        "prop": "text|sections",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "maxlag": "5",
    }
    errors: list[str] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(
                API,
                params=params,
                headers=HEADERS,
                timeout=(10, 60),
            )
            print(
                f"GET {season_label(start_year)} attempt={attempt}/{MAX_ATTEMPTS} "
                f"status={response.status_code} bytes={len(response.content)}",
                flush=True,
            )

            if response.status_code in RETRYABLE_STATUS_CODES:
                header_wait = retry_after_seconds(response)
                exponential_wait = min(30 * (2 ** (attempt - 1)), 600)
                jitter = random.uniform(2, 10)
                wait_seconds = max(header_wait or 0, exponential_wait) + jitter
                errors.append(
                    f"HTTP {response.status_code} attempt {attempt}; wait={wait_seconds:.1f}s"
                )
                wait_with_progress(
                    wait_seconds,
                    f"Wikipedia retry for {season_label(start_year)}",
                )
                continue

            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                error = payload["error"]
                code = str(error.get("code") or "") if isinstance(error, dict) else ""
                if code in {"maxlag", "ratelimited", "readonly"}:
                    wait_seconds = min(30 * (2 ** (attempt - 1)), 600) + random.uniform(2, 10)
                    errors.append(f"MediaWiki {code} attempt {attempt}")
                    wait_with_progress(
                        wait_seconds,
                        f"MediaWiki {code} for {season_label(start_year)}",
                    )
                    continue
                raise RuntimeError(f"MediaWiki error: {error}")

            raw_html = str(payload.get("parse", {}).get("text") or "")
            if not raw_html:
                raise RuntimeError("MediaWiki returned empty page HTML")

            atomic_write_json_gz(paths["payload"], payload)
            atomic_write_text_gz(paths["html"], raw_html)
            return raw_html, "remote"
        except requests.RequestException as exc:
            errors.append(repr(exc))
            if attempt < MAX_ATTEMPTS:
                wait_seconds = min(20 * (2 ** (attempt - 1)), 300) + random.uniform(2, 10)
                wait_with_progress(
                    wait_seconds,
                    f"network retry for {season_label(start_year)}",
                )
        except Exception as exc:
            errors.append(repr(exc))
            break

    raise RuntimeError("; ".join(errors))


def available_season_data() -> tuple[dict[int, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
    rows_by_year: dict[int, list[dict[str, Any]]] = {}
    summaries_by_year: dict[int, dict[str, Any]] = {}
    for start_year in START_YEARS:
        cached = load_cached_rows(start_year)
        if cached is None:
            continue
        rows, summary = cached
        rows_by_year[start_year] = rows
        summaries_by_year[start_year] = summary
    return rows_by_year, summaries_by_year


def mutombo_rows(all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in all_rows:
        if row.get("season") != "2000-01":
            continue
        text = " ".join(str(cell) for cell in row.get("cells", []))
        link_text = " ".join(
            f"{link.get('text', '')} {link.get('title', '')}"
            for link in row.get("links", [])
        )
        combined = f"{text} {link_text}"
        if (
            "Dikembe Mutombo" in combined
            and "Atlanta Hawks" in combined
            and "Philadelphia 76ers" in combined
            and (row.get("date_hint") == "February 22" or "February 22" in combined)
        ):
            matches.append(row)
    return matches


def write_combined_outputs(
    failures: list[dict[str, Any]],
    interrupted: bool = False,
) -> dict[str, Any]:
    rows_by_year, summaries_by_year = available_season_data()
    all_rows = [
        row
        for start_year in sorted(rows_by_year)
        for row in rows_by_year[start_year]
    ]

    # Never destroy an existing valid combined archive with an empty result.
    if all_rows:
        atomic_write_jsonl_gz(ROWS_PATH, all_rows)
    elif not ROWS_PATH.exists():
        print("NO ROWS AVAILABLE: combined rows archive not written", flush=True)

    section_counts: dict[str, int] = {}
    for row in all_rows:
        section = str(row.get("section") or "Unsectioned")
        section_counts[section] = section_counts.get(section, 0) + 1

    mutombo_matches = mutombo_rows(all_rows)
    completed_years = sorted(rows_by_year)
    unresolved_years = [year for year in START_YEARS if year not in rows_by_year]
    failure_by_season = {
        str(item.get("season")): item
        for item in failures
        if item.get("season")
    }

    summary = {
        "generated_at_utc": utc_now(),
        "source": API,
        "requested_seasons": len(START_YEARS),
        "completed_seasons": len(completed_years),
        "completed_season_labels": [season_label(year) for year in completed_years],
        "unresolved_seasons": [season_label(year) for year in unresolved_years],
        "failed_seasons": [
            failure_by_season[season_label(year)]
            for year in unresolved_years
            if season_label(year) in failure_by_season
        ],
        "total_rows": len(all_rows),
        "section_row_counts": section_counts,
        "mutombo_2001_row_count": len(mutombo_matches),
        "mutombo_2001_rows": mutombo_matches,
        "page_summaries": [summaries_by_year[year] for year in completed_years],
        "rows_file": str(ROWS_PATH),
        "raw_payload_directory": str(RAW_DIR),
        "season_rows_directory": str(PAGE_ROWS_DIR),
        "interrupted": interrupted,
        "complete": (
            len(completed_years) == len(START_YEARS)
            and not unresolved_years
            and bool(all_rows)
            and bool(mutombo_matches)
        ),
    }
    atomic_write_json(SUMMARY_PATH, summary)
    atomic_write_json(FAILURES_PATH, summary["failed_seasons"])
    return summary


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PAGE_ROWS_DIR.mkdir(parents=True, exist_ok=True)
    PAGE_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    failures: list[dict[str, Any]] = []

    initial_rows, _ = available_season_data()
    print(
        f"RESUME: {len(initial_rows)}/{len(START_YEARS)} seasons already have parsed row caches",
        flush=True,
    )

    try:
        for position, start_year in enumerate(START_YEARS, start=1):
            label = season_label(start_year)
            cached = load_cached_rows(start_year)
            if cached is not None:
                rows, summary = cached
                print(
                    f"SKIP {position}/{len(START_YEARS)} {label}: "
                    f"parsed cache rows={len(rows)} source={summary.get('source')}",
                    flush=True,
                )
                continue

            print(f"START {position}/{len(START_YEARS)} {label}", flush=True)
            try:
                raw_html, source = fetch_page(session, start_year)
                rows, summary = parse_page(start_year, raw_html)
                if not rows:
                    raise RuntimeError("page was available but no table rows were parsed")

                paths = cache_paths(start_year)
                summary.update(
                    {
                        "raw_api_payload": str(paths["payload"]),
                        "raw_html": str(paths["html"]),
                        "season_rows": str(paths["rows"]),
                        "source": source,
                        "completed_at_utc": utc_now(),
                    }
                )
                atomic_write_jsonl_gz(paths["rows"], rows)
                atomic_write_json(paths["summary"], summary)

                print(
                    f"ARCHIVED {label} source={source} "
                    f"tables={summary['tables']} rows={summary['rows']}",
                    flush=True,
                )

                progress = write_combined_outputs(failures)
                print(
                    f"CHECKPOINT {progress['completed_seasons']}/{len(START_YEARS)} "
                    f"seasons rows={progress['total_rows']} "
                    f"unresolved={len(progress['unresolved_seasons'])}",
                    flush=True,
                )

                if source == "remote":
                    wait_with_progress(
                        REMOTE_REQUEST_PAUSE_SECONDS + random.uniform(0, 5),
                        "polite pause between Wikipedia requests",
                    )
            except Exception as exc:
                failure = {
                    "season": label,
                    "start_year": start_year,
                    "error": repr(exc),
                    "failed_at_utc": utc_now(),
                }
                failures.append(failure)
                print(f"FAILED {label}: {exc!r}", file=sys.stderr, flush=True)
                progress = write_combined_outputs(failures)
                print(
                    f"FAILURE RECORDED: completed={progress['completed_seasons']} "
                    f"unresolved={len(progress['unresolved_seasons'])}",
                    flush=True,
                )
    except KeyboardInterrupt:
        summary = write_combined_outputs(failures, interrupted=True)
        print(
            f"INTERRUPTED SAFELY: completed={summary['completed_seasons']} "
            f"rows={summary['total_rows']}",
            file=sys.stderr,
            flush=True,
        )
        return 130

    summary = write_combined_outputs(failures)
    print(
        json.dumps(
            {
                "complete": summary["complete"],
                "completed_seasons": summary["completed_seasons"],
                "completed_season_labels": summary["completed_season_labels"],
                "unresolved_seasons": summary["unresolved_seasons"],
                "failed_seasons": summary["failed_seasons"],
                "total_rows": summary["total_rows"],
                "section_row_counts": summary["section_row_counts"],
                "mutombo_2001_row_count": summary["mutombo_2001_row_count"],
                "rows_file": summary["rows_file"],
                "summary_file": str(SUMMARY_PATH),
                "failures_file": str(FAILURES_PATH),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
