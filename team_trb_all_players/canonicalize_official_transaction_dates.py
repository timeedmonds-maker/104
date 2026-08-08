from __future__ import annotations

import argparse
import gzip
import json
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
SUMMARY = ROOT / "official_transaction_date_canonicalization_summary.json"
OFFICIAL_SOURCE = "Official NBA Player Movement feed"


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def canonical_date(value: object) -> str:
    text = str(value or "").strip()
    if len(text) < 10:
        raise ValueError(f"official transaction date is too short: {text!r}")
    day = text[:10]
    # Validates rather than blindly truncating malformed strings.
    return date.fromisoformat(day).isoformat()


def season_for_day(day: str) -> str:
    parsed = date.fromisoformat(day)
    start_year = parsed.year if parsed.month >= 7 else parsed.year - 1
    return season_label(start_year)


def self_test() -> None:
    assert canonical_date("2024-02-01T00:00:00") == "2024-02-01"
    assert canonical_date("2024-02-01") == "2024-02-01"
    assert canonical_date("2024-02-01T00:00:00Z") == "2024-02-01"
    assert season_for_day("2024-02-01") == "2023-24"
    assert season_for_day("2024-07-01") == "2024-25"
    print("OFFICIAL TRANSACTION DATE CANONICALIZATION SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    if not EVENTS.exists():
        raise RuntimeError(f"Normalized transaction file missing: {EVENTS}")

    rows: list[dict] = []
    official_rows = 0
    corrected_dates = 0
    corrected_seasons = 0
    failures: list[dict] = []

    with gzip.open(EVENTS, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("source_system") == OFFICIAL_SOURCE:
                official_rows += 1
                original_date = str(row.get("exact_date") or "").strip()
                original_season = str(row.get("season") or "")
                try:
                    day = canonical_date(original_date)
                    season = season_for_day(day)
                except Exception as exc:
                    failures.append(
                        {
                            "line_number": line_number,
                            "player_id": row.get("player_id"),
                            "exact_date": original_date,
                            "season": original_season,
                            "error": repr(exc),
                        }
                    )
                else:
                    if day != original_date:
                        corrected_dates += 1
                    if season != original_season:
                        corrected_seasons += 1
                    row["exact_date"] = day
                    row["season"] = season
            rows.append(row)

    if failures:
        SUMMARY.write_text(
            json.dumps(
                {
                    "official_rows": official_rows,
                    "corrected_dates": corrected_dates,
                    "corrected_seasons": corrected_seasons,
                    "failure_count": len(failures),
                    "failures": failures[:100],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise RuntimeError(f"Unable to canonicalize {len(failures)} official transaction dates; see {SUMMARY}")

    rows.sort(key=lambda row: (str(row.get("exact_date") or ""), str(row.get("player_id") or ""), str(row.get("event_type") or ""), str(row.get("source_reference") or "")))
    tmp = EVENTS.with_suffix(EVENTS.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(EVENTS)

    summary = {
        "official_rows": official_rows,
        "corrected_dates": corrected_dates,
        "corrected_seasons": corrected_seasons,
        "failure_count": 0,
        "output": str(EVENTS),
        "note": "Canonicalizes official NBA Player Movement ISO datetimes to YYYY-MM-DD and derives the NBA season from the canonical calendar date before tenure construction.",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
