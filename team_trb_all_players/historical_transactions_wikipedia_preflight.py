from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "historical_transactions_wikipedia_preflight.json"
API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": "TREB-historical-roster-research/1.0 (GitHub: timeedmonds-maker/104)",
    "Accept": "application/json",
}


def season_label(start_year: int) -> str:
    return f"{start_year}\u2013{str(start_year + 1)[-2:]}"


def page_title(start_year: int) -> str:
    return f"List of {season_label(start_year)} NBA season transactions"


def get_json(params: dict[str, str]) -> dict[str, Any]:
    response = requests.get(API, params=params, headers=HEADERS, timeout=(10, 30))
    print(f"GET {response.url}", flush=True)
    print(f"  status={response.status_code} bytes={len(response.content)}", flush=True)
    response.raise_for_status()
    return response.json()


def strip_html(value: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    titles = [page_title(year) for year in range(2000, 2016)]
    query = get_json(
        {
            "action": "query",
            "titles": "|".join(titles),
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
    )
    pages = query.get("query", {}).get("pages", [])
    resolved = {str(page.get("title")): page for page in pages}
    missing = [title for title in titles if title not in resolved or resolved[title].get("missing")]

    target = page_title(2000)
    parsed = get_json(
        {
            "action": "parse",
            "page": target,
            "prop": "text|sections",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
    )
    parse = parsed.get("parse", {})
    raw_html = str(parse.get("text") or "")
    text = strip_html(raw_html)

    checks = {
        "dikembe_mutombo": "Dikembe Mutombo" in text,
        "philadelphia_76ers": "Philadelphia 76ers" in text,
        "atlanta_hawks": "Atlanta Hawks" in text,
        "february_22": "February 22" in text,
        "trade_context": bool(
            re.search(
                r"February 22.{0,700}Philadelphia 76ers.{0,700}Dikembe Mutombo.{0,700}Atlanta Hawks",
                text,
                flags=re.I | re.S,
            )
        ),
    }
    sections = [str(item.get("line") or "") for item in parse.get("sections", [])]
    expected_sections = {"Trades", "Released", "Signings"}
    section_check = expected_sections.issubset(set(sections))

    report = {
        "source": API,
        "page_coverage": {
            "requested": len(titles),
            "resolved": len(titles) - len(missing),
            "missing": missing,
            "titles": titles,
        },
        "mutombo_preflight": {
            "page": target,
            "html_bytes": len(raw_html.encode("utf-8")),
            "sections": sections,
            "checks": checks,
            "expected_sections_present": section_check,
        },
    }
    report["preflight_passed"] = not missing and all(checks.values()) and section_check

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "preflight_passed": report["preflight_passed"],
                "page_coverage": report["page_coverage"],
                "mutombo_checks": checks,
                "expected_sections_present": section_check,
                "report": str(OUT),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if report["preflight_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
