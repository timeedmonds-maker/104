from __future__ import annotations

import gzip
import json
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import build_historical_transactions_archive as original


BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "impact_database" / "historical_transactions"
RAW_DIR = OUT_DIR / "raw_pages"
API_DIR = OUT_DIR / "raw_api_responses"
SEASON_ROWS_DIR = OUT_DIR / "season_rows"
SEASON_SUMMARY_DIR = OUT_DIR / "season_summaries"
ROWS_PATH = OUT_DIR / "wikipedia_transaction_rows.jsonl.gz"
SUMMARY_PATH = OUT_DIR / "archive_summary.json"
FAILURES_PATH = OUT_DIR / "archive_failures.json"

API = "https://en.wikipedia.org/w/api.php"
START_YEARS = list(range(2000, 2016))
REQUEST_GAP_SECONDS = 8.0
MAX_ATTEMPTS = 6

HEADERS = {
    "User-Agent": (
        "TREB-historical-roster-research/2.0 "
        "(GitHub repository: timeedmonds-maker/104; "
        "purpose: auditable NBA roster-tenure research)"
    ),
    "Accept": "application/json",
}

_last_request_at = 0.0


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_gzip_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def wait_for_request_slot() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    remaining = REQUEST_GAP_SECONDS - elapsed
    if remaining > 0:
        print(f"  rate-limit spacing: sleeping {remaining:.1f}s", flush=True)
        time.sleep(remaining)


def fetch_page(start_year: int) -> tuple[str, dict[str, Any]]:
    global _last_request_at

    title = original.page_title(start_year)
    params = {
        "action": "parse",
        "page": title,
        "prop": "text|sections",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }

    errors: list[dict[str, Any]] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait_for_request_slot()
        print(
            f"  GET Wikipedia attempt {attempt}/{MAX_ATTEMPTS}: {title}",
            flush=True,
        )

        try:
            response = requests.get(
                API,
                params=params,
                headers=HEADERS,
                timeout=(10, 45),
            )
            _last_request_at = time.monotonic()

            print(
                f"  status={response.status_code} bytes={len(response.content)}",
                flush=True,
            )

            if response.status_code == 429 or 500 <= response.status_code < 600:
                retry_after = response.headers.get("Retry-After", "").strip()
                if retry_after.isdigit():
                    wait_seconds = float(retry_after)
                else:
                    wait_seconds = min(120.0, 8.0 * (2 ** (attempt - 1)))

                wait_seconds += random.uniform(1.0, 4.0)
                errors.append(
                    {
                        "attempt": attempt,
                        "status": response.status_code,
                        "retry_after": retry_after or None,
                        "wait_seconds": round(wait_seconds, 2),
                    }
                )

                if attempt < MAX_ATTEMPTS:
                    print(
                        f"  transient response; sleeping {wait_seconds:.1f}s",
                        flush=True,
                    )
                    time.sleep(wait_seconds)
                    continue

            response.raise_for_status()
            payload = response.json()
            parse = payload.get("parse", {})
            raw_html = str(parse.get("text") or "")

            if not raw_html:
                raise RuntimeError("Wikipedia response contained no parsed HTML")

            return raw_html, payload

        except Exception as exc:
            errors.append({"attempt": attempt, "error": repr(exc)})

            if attempt < MAX_ATTEMPTS:
                wait_seconds = min(120.0, 8.0 * (2 ** (attempt - 1)))
                wait_seconds += random.uniform(1.0, 4.0)
                print(
                    f"  request failed; sleeping {wait_seconds:.1f}s: {exc!r}",
                    flush=True,
                )
                time.sleep(wait_seconds)

    raise RuntimeError(json.dumps(errors, ensure_ascii=False))


def write_season_rows(season: str, rows: list[dict[str, Any]]) -> Path:
    path = SEASON_ROWS_DIR / f"{season}.jsonl.gz"
    content = "".join(
        json.dumps(row, ensure_ascii=False) + "\n"
        for row in rows
    )
    atomic_gzip_text(path, content)
    return path


def rebuild_combined_rows() -> int:
    available_paths = [
        SEASON_ROWS_DIR / f"{original.season_label(year)}.jsonl.gz"
        for year in START_YEARS
    ]
    available_paths = [path for path in available_paths if path.exists()]

    if not available_paths:
        print("  no valid season row files; existing combined archive preserved", flush=True)
        return 0

    temporary = ROWS_PATH.with_suffix(ROWS_PATH.suffix + ".tmp")
    total_rows = 0

    with gzip.open(temporary, "wt", encoding="utf-8") as output:
        for path in available_paths:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                for line in source:
                    if line.strip():
                        output.write(line)
                        total_rows += 1

    if total_rows > 0:
        os.replace(temporary, ROWS_PATH)
    elif temporary.exists():
        temporary.unlink()

    return total_rows


def read_season_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def build_summary(
    failures: list[dict[str, Any]],
    run_started: str,
) -> dict[str, Any]:
    completed: list[str] = []
    all_rows: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []

    for year in START_YEARS:
        season = original.season_label(year)
        rows_path = SEASON_ROWS_DIR / f"{season}.jsonl.gz"
        summary_path = SEASON_SUMMARY_DIR / f"{season}.json"

        if rows_path.exists() and summary_path.exists():
            completed.append(season)
            all_rows.extend(read_season_rows(rows_path))
            page_summaries.append(
                json.loads(summary_path.read_text(encoding="utf-8"))
            )

    section_counts = Counter(str(row.get("section") or "") for row in all_rows)

    mutombo_matches = []
    for row in all_rows:
        if str(row.get("season")) != "2000-01":
            continue
        searchable = json.dumps(row, ensure_ascii=False).casefold()
        if (
            "dikembe mutombo" in searchable
            and "philadelphia 76ers" in searchable
            and "atlanta hawks" in searchable
            and "february 22" in searchable
        ):
            mutombo_matches.append(row)

    requested = [original.season_label(year) for year in START_YEARS]
    unresolved = [season for season in requested if season not in completed]

    return {
        "run_started_utc": run_started,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "requested_seasons": requested,
        "completed_seasons": completed,
        "unresolved_seasons": unresolved,
        "failed_seasons": failures,
        "pages_completed": len(completed),
        "pages_requested": len(requested),
        "total_rows": len(all_rows),
        "section_row_counts": dict(sorted(section_counts.items())),
        "mutombo_2001_row_count": len(mutombo_matches),
        "mutombo_2001_validated": bool(mutombo_matches),
        "complete": (
            len(completed) == len(requested)
            and not unresolved
            and bool(mutombo_matches)
        ),
        "page_summaries": page_summaries,
        "rows_file": str(ROWS_PATH),
        "failures_file": str(FAILURES_PATH),
    }


def persist_state(
    failures: list[dict[str, Any]],
    run_started: str,
) -> dict[str, Any]:
    total_rows = rebuild_combined_rows()
    summary = build_summary(failures, run_started)
    summary["combined_rows_written"] = total_rows

    atomic_text(
        SUMMARY_PATH,
        json.dumps(summary, indent=2, ensure_ascii=False),
    )
    atomic_text(
        FAILURES_PATH,
        json.dumps(failures, indent=2, ensure_ascii=False),
    )
    return summary


def main() -> int:
    for directory in (
        OUT_DIR,
        RAW_DIR,
        API_DIR,
        SEASON_ROWS_DIR,
        SEASON_SUMMARY_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    run_started = datetime.now(timezone.utc).isoformat()
    failures: list[dict[str, Any]] = []

    for index, start_year in enumerate(START_YEARS, start=1):
        season = original.season_label(start_year)
        raw_path = RAW_DIR / f"{season}.html.gz"
        api_path = API_DIR / f"{season}.json.gz"

        print(f"[{index}/{len(START_YEARS)}] {season}", flush=True)

        if start_year in {2001, 2002}:
            failure = {"season": season, "error": "No league-wide Wikipedia transaction page exists; alternate source required.", "failed_utc": datetime.now(timezone.utc).isoformat()}
            failures.append(failure)
            persist_state(failures, run_started)
            print(f"  DEFERRED {season}: alternate source required", flush=True)
            continue

        try:
            if raw_path.exists() and raw_path.stat().st_size > 100:
                print(
                    f"  CACHE raw HTML: {raw_path.name} "
                    f"({raw_path.stat().st_size} bytes)",
                    flush=True,
                )
                with gzip.open(raw_path, "rt", encoding="utf-8") as handle:
                    raw_html = handle.read()
                source = "cached_raw_html"
                api_sections: list[str] = []
            else:
                print("  FETCH missing raw HTML", flush=True)
                raw_html, payload = fetch_page(start_year)
                atomic_gzip_text(raw_path, raw_html)
                atomic_gzip_text(
                    api_path,
                    json.dumps(payload, ensure_ascii=False),
                )
                source = "wikipedia_api"
                api_sections = [
                    original.clean_text(str(item.get("line") or ""))
                    for item in payload.get("parse", {}).get("sections", [])
                ]

            rows, page_summary = original.parse_page(start_year, raw_html)

            if not rows:
                raise RuntimeError(
                    f"parsed zero transaction rows for {season}"
                )

            rows_path = write_season_rows(season, rows)
            page_summary.update(
                {
                    "source": source,
                    "raw_page": str(raw_path),
                    "api_response": str(api_path) if api_path.exists() else None,
                    "api_sections": api_sections,
                    "season_rows_file": str(rows_path),
                    "completed_utc": datetime.now(timezone.utc).isoformat(),
                }
            )

            atomic_text(
                SEASON_SUMMARY_DIR / f"{season}.json",
                json.dumps(page_summary, indent=2, ensure_ascii=False),
            )

            summary = persist_state(failures, run_started)
            print(
                f"  COMPLETE {season}: rows={len(rows)}; "
                f"archive={summary['total_rows']} rows; "
                f"pages={summary['pages_completed']}/16",
                flush=True,
            )

        except KeyboardInterrupt:
            persist_state(failures, run_started)
            print("Interrupted safely; checkpoints preserved.", flush=True)
            raise

        except Exception as exc:
            failure = {
                "season": season,
                "error": repr(exc),
                "failed_utc": datetime.now(timezone.utc).isoformat(),
            }
            failures.append(failure)
            persist_state(failures, run_started)
            print(f"  FAILED {season}: {exc!r}", flush=True)

    final_summary = persist_state(failures, run_started)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False), flush=True)

    return 0 if final_summary["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
