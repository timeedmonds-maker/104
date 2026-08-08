from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
NORMALIZED = ROOT / "normalized_transactions.jsonl.gz"
CACHE = ROOT / "realgm_transactions"
SUMMARY = ROOT / "realgm_enrichment_summary.json"
YEARS = list(range(2000, 2016))
DATE_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}$")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TEAM_OVERRIDES = {
    "charlotte hornets (1988)": 1610612740,
    "charlotte hornets": 1610612766,
    "charlotte bobcats": 1610612766,
    "new orleans hornets": 1610612740,
    "new orleans pelicans": 1610612740,
    "new jersey nets": 1610612751,
    "brooklyn nets": 1610612751,
    "seattle supersonics": 1610612760,
    "oklahoma city thunder": 1610612760,
    "vancouver grizzlies": 1610612763,
    "memphis grizzlies": 1610612763,
    "philadelphia sixers": 1610612755,
    "philadelphia 76ers": 1610612755,
}


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def page_url(year: int) -> str:
    return f"https://basketball.realgm.com/nba/transactions/league/{year + 1}"


def get(session: requests.Session, url: str, attempts: int = 4) -> str:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            r = session.get(url, headers=HEADERS, timeout=(10, 45))
            r.raise_for_status()
            if len(r.text) < 5000:
                raise RuntimeError(f"suspiciously short RealGM page: {len(r.text)} chars")
            return r.text
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"RealGM fetch failed for {url}: {last!r}")


def team_id(name: str | None, season: str, context: norm.CoreContext) -> int | None:
    if not name:
        return None
    cleaned = re.sub(r"\s+", " ", name).strip().rstrip(".")
    override = TEAM_OVERRIDES.get(cleaned.casefold())
    if override:
        return override
    value, _ = norm.resolve_team(cleaned, season, context)
    return value


def resolve_player(name: str, season: str, context: norm.CoreContext, src: int | None, dst: int | None) -> str | None:
    pid, _ = norm.resolve_player(name, season, context, src, dst)
    if pid:
        return pid
    # RealGM occasionally uses full first names where the core has an abbreviation.
    target = norm.normalize_name(name)
    surname = target.split()[-1] if target else ""
    candidates: set[str] = set()
    for (s, pid0), teams in context.affiliations.items():
        if s != season:
            continue
        if src is not None or dst is not None:
            if not (teams & {x for x in (src, dst) if x is not None}):
                continue
        for core_name in context.names_by_id.get(pid0, set()):
            cn = norm.normalize_name(core_name)
            if surname and cn.split()[-1:] == [surname]:
                candidates.add(pid0)
    return next(iter(candidates)) if len(candidates) == 1 else None


def make_event(*, day: str, season: str, kind: str, player: str, src_name: str | None, dst_name: str | None,
               raw: str, context: norm.CoreContext, subtype: str) -> dict[str, Any] | None:
    src = team_id(src_name, season, context)
    dst = team_id(dst_name, season, context)
    pid = resolve_player(player, season, context, src, dst)
    if not pid:
        return None
    return {
        "exact_date": datetime.strptime(day, "%b %d, %Y").date().isoformat(),
        "season": season,
        "event_type": kind,
        "player_id": pid,
        "player_name": player,
        "source_player_ref": None,
        "source_team_id": src,
        "destination_team_id": dst,
        "source_team_name": src_name,
        "destination_team_name": dst_name,
        "source_system": "RealGM league transaction history",
        "source_reference": page_url(int(season[:4])),
        "identity_resolution": "realgm_name+core_team_context",
        "team_resolution": "realgm_historical_team_alias",
        "raw_text": raw,
        "confidence": "high",
        "realgm_subtype": subtype,
    }


def parse_transaction(day: str, season: str, text: str, context: norm.CoreContext) -> list[dict[str, Any]]:
    t = re.sub(r"\s+", " ", text).strip()
    out: list[dict[str, Any]] = []
    patterns = [
        ("depart", "waiver", re.compile(r"^The (?P<team>.+?) placed the contract of (?P<player>.+?) on waivers\.?$", re.I)),
        ("depart", "terminated_10day", re.compile(r"^The (?P<team>.+?) terminated the 10[- ]day contract for (?P<player>.+?)\.?$", re.I)),
        ("depart", "free_agent", re.compile(r"^(?P<player>.+?), previously with the (?P<team>.+?), became a free agent\.?$", re.I)),
        ("claim", "waiver_claim", re.compile(r"^The (?P<team>.+?) made a successful waiver claim for the contract of (?P<player>.+?)\.?$", re.I)),
        ("acquire", "signing", re.compile(r"^(?P<player>.+?) signed (?:a|an) .+? contract with the (?P<team>.+?)\.?$", re.I)),
        ("acquire", "signing", re.compile(r"^(?P<player>.+?) signed a contract with the (?P<team>.+?)\.?$", re.I)),
        ("acquire", "signing", re.compile(r"^(?P<player>.+?) signed with the (?P<team>.+?)\.?$", re.I)),
    ]
    for kind, subtype, pat in patterns:
        m = pat.match(t)
        if not m:
            continue
        team = m.group("team").strip()
        e = make_event(day=day, season=season, kind=kind, player=m.group("player").strip(),
                       src_name=team if kind == "depart" else None,
                       dst_name=team if kind in {"acquire", "claim"} else None,
                       raw=t, context=context, subtype=subtype)
        return [e] if e else []

    # Trade-acquisition rows on RealGM are player-first and explicitly name both teams.
    m = re.match(r"^(?P<player>.+?) was acquired by the (?P<dst>.+?) from the (?P<src>.+?)(?: in exchange for:.*)?\.?$", t, re.I)
    if m:
        e = make_event(day=day, season=season, kind="trade", player=m.group("player").strip(),
                       src_name=m.group("src").strip().rstrip("."), dst_name=m.group("dst").strip(),
                       raw=t, context=context, subtype="trade")
        return [e] if e else []
    return out


def parse_page(year: int, html: str, context: norm.CoreContext) -> list[dict[str, Any]]:
    season = season_label(year)
    soup = BeautifulSoup(html, "html.parser")
    current_day: str | None = None
    events: list[dict[str, Any]] = []
    for raw in soup.stripped_strings:
        text = re.sub(r"\s+", " ", str(raw)).strip()
        if DATE_RE.match(text):
            current_day = text
            continue
        if current_day is None:
            continue
        if any(key in text.casefold() for key in (
            "became a free agent", "on waivers", "terminated the 10", "signed a contract",
            "signed a ", "signed with the ", "successful waiver claim", "was acquired by the "
        )):
            events.extend(parse_transaction(current_day, season, text, context))
    return events


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Same-team free-agent + re-sign on the same date is continuous roster service.
    by_date_team_player: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for e in events:
        team = int(e.get("source_team_id") or e.get("destination_team_id") or 0)
        by_date_team_player.setdefault((str(e["exact_date"]), str(e["player_id"]), team), []).append(e)
    cancelled: set[int] = set()
    for group in by_date_team_player.values():
        has_in = any(e["event_type"] in {"acquire", "claim"} for e in group)
        if has_in:
            for e in group:
                if e.get("realgm_subtype") == "free_agent":
                    cancelled.add(id(e))
                    # cancel only the matching same-day acquisition too; continuity needs no boundary
                    for x in group:
                        if x["event_type"] in {"acquire", "claim"}:
                            cancelled.add(id(x))

    filtered = [e for e in events if id(e) not in cancelled]
    # If a waiver/termination exists shortly before free agency, roster departure is the waiver/termination date.
    final: list[dict[str, Any]] = []
    for e in filtered:
        if e.get("realgm_subtype") == "free_agent":
            day = datetime.fromisoformat(str(e["exact_date"])).date()
            src = e.get("source_team_id")
            earlier = [x for x in filtered if x.get("player_id") == e.get("player_id") and x.get("source_team_id") == src
                       and x.get("realgm_subtype") in {"waiver", "terminated_10day"}]
            if any(0 <= (day - datetime.fromisoformat(str(x["exact_date"])).date()).days <= 3 for x in earlier):
                continue
        final.append(e)
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for e in sorted(final, key=lambda x: (x["exact_date"], x["player_id"], x["event_type"], x.get("realgm_subtype", ""))):
        key = (e["exact_date"], e["season"], e["event_type"], e["player_id"], e.get("source_team_id"), e.get("destination_team_id"))
        if key not in seen:
            seen.add(key); out.append(e)
    return out


def load_normalized() -> list[dict[str, Any]]:
    with gzip.open(NORMALIZED, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_normalized(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda e: (str(e.get("exact_date") or ""), str(e.get("player_id") or ""), str(e.get("event_type") or ""), str(e.get("source_reference") or "")))
    with gzip.open(NORMALIZED, "wt", encoding="utf-8") as f:
        for e in rows:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def self_test() -> None:
    assert DATE_RE.match("Jan 26, 2001")
    print("RealGM enrichment self-test PASSED")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--self-test", action="store_true"); args = ap.parse_args()
    if args.self_test:
        self_test(); return 0
    context = norm.load_core_context()
    session = requests.Session()
    all_events: list[dict[str, Any]] = []
    season_counts: dict[str, int] = {}
    CACHE.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        season = season_label(year)
        raw_path = CACHE / f"{season}.html.gz"
        if raw_path.exists():
            with gzip.open(raw_path, "rt", encoding="utf-8") as f: html = f.read()
            cache = "hit"
        else:
            html = get(session, page_url(year)); cache = "network"
            with gzip.open(raw_path, "wt", encoding="utf-8") as f: f.write(html)
            time.sleep(0.25)
        parsed = dedupe(parse_page(year, html, context))
        if len(parsed) < 20:
            raise RuntimeError(f"{season}: RealGM parser produced only {len(parsed)} roster events ({cache})")
        season_counts[season] = len(parsed); all_events.extend(parsed)
        print(f"REALGM {season}: {len(parsed)} roster events ({cache})", flush=True)

    existing = load_normalized()
    existing_keys = {(str(e.get("exact_date") or ""), str(e.get("season") or ""), str(e.get("event_type") or ""), str(e.get("player_id") or ""), int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0)) for e in existing}
    added = []
    for e in all_events:
        key = (e["exact_date"], e["season"], e["event_type"], str(e["player_id"]), int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0))
        if key not in existing_keys:
            existing_keys.add(key); existing.append(e); added.append(e)
    write_normalized(existing)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "seasons": len(YEARS),
        "parsed_realgm_events": len(all_events),
        "added_events": len(added),
        "season_counts": season_counts,
        "source": "RealGM NBA league transaction history",
        "purpose": "exact historical waiver, free-agent, 10-day-expiry, signing and trade boundaries missing from Basketball-Reference transaction prose",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
