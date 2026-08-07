from __future__ import annotations

import argparse
import gzip
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
BREF = IMPACT / "historical_transactions" / "basketball_reference_uniform" / "season_rows"
CHECKPOINTS = IMPACT / "core_checkpoints"
OUT = IMPACT / "roster_tenure"
NORMALIZED = OUT / "normalized_transactions.jsonl.gz"
UNRESOLVED = OUT / "normalization_unresolved.json"
SUMMARY = OUT / "normalization_summary.json"
OFFICIAL_RAW = OUT / "official_movement_feed.json.gz"

MOVEMENT_URLS = [
    "https://stats.nba.com/js/data/playermovement/NBA_Player_Movement.json",
    "https://www.nba.com/stats/js/data/playermovement/NBA_Player_Movement.json",
]
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
}

# Candidate IDs are intentionally allowed here. The active team IDs in each
# core season disambiguate historical franchise names (especially Charlotte).
TEAM_ALIASES: dict[str, tuple[int, ...]] = {
    "Atlanta Hawks": (1610612737,),
    "Boston Celtics": (1610612738,),
    "Cleveland Cavaliers": (1610612739,),
    "Charlotte Hornets": (1610612740, 1610612766),
    "New Orleans Hornets": (1610612740,),
    "New Orleans/Oklahoma City Hornets": (1610612740,),
    "New Orleans Pelicans": (1610612740,),
    "Chicago Bulls": (1610612741,),
    "Dallas Mavericks": (1610612742,),
    "Denver Nuggets": (1610612743,),
    "Golden State Warriors": (1610612744,),
    "Houston Rockets": (1610612745,),
    "Los Angeles Clippers": (1610612746,),
    "LA Clippers": (1610612746,),
    "Los Angeles Lakers": (1610612747,),
    "Miami Heat": (1610612748,),
    "Milwaukee Bucks": (1610612749,),
    "Minnesota Timberwolves": (1610612750,),
    "New Jersey Nets": (1610612751,),
    "Brooklyn Nets": (1610612751,),
    "New York Knicks": (1610612752,),
    "Orlando Magic": (1610612753,),
    "Indiana Pacers": (1610612754,),
    "Philadelphia 76ers": (1610612755,),
    "Phoenix Suns": (1610612756,),
    "Portland Trail Blazers": (1610612757,),
    "Sacramento Kings": (1610612758,),
    "San Antonio Spurs": (1610612759,),
    "Seattle SuperSonics": (1610612760,),
    "Seattle Super Sonics": (1610612760,),
    "Oklahoma City Thunder": (1610612760,),
    "Toronto Raptors": (1610612761,),
    "Utah Jazz": (1610612762,),
    "Vancouver Grizzlies": (1610612763,),
    "Memphis Grizzlies": (1610612763,),
    "Washington Wizards": (1610612764,),
    "Detroit Pistons": (1610612765,),
    "Charlotte Bobcats": (1610612766,),
}
TEAM_PATTERN = "|".join(re.escape(name) for name in sorted(TEAM_ALIASES, key=len, reverse=True))

TRADE_CLAUSE_RE = re.compile(
    rf"(?:^|;|\band\s+)\s*(?:The|the)\s+(?P<src>{TEAM_PATTERN})\s+traded\s+"
    rf"(?P<assets>.+?)\s+to\s+the\s+(?P<dst>{TEAM_PATTERN})"
    rf"(?:\s+for\s+(?P<returns>.+?))?(?=\s*;|\s*\.|$)",
    re.IGNORECASE,
)
TEAM_VERB_RE = {
    "signed": re.compile(rf"(?:The|the)\s+(?P<team>{TEAM_PATTERN})\s+signed\s+(?P<body>.+?)(?=\.|$)", re.IGNORECASE),
    "waived": re.compile(rf"(?:The|the)\s+(?P<team>{TEAM_PATTERN})\s+waived\s+(?P<body>.+?)(?=\.|$)", re.IGNORECASE),
    "released": re.compile(rf"(?:The|the)\s+(?P<team>{TEAM_PATTERN})\s+released\s+(?P<body>.+?)(?=\.|$)", re.IGNORECASE),
    "claimed": re.compile(rf"(?:The|the)\s+(?P<team>{TEAM_PATTERN})\s+claimed\s+(?P<body>.+?)(?=\.|$)", re.IGNORECASE),
}


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if text.isdigit() and text != "0" else ""


def ci(row: dict[str, Any], *keys: str) -> Any:
    lookup = {str(key).casefold(): value for key, value in row.items()}
    for key in keys:
        if key.casefold() in lookup:
            return lookup[key.casefold()]
    return None


def movement_rows(payload: Any) -> list[dict[str, Any]]:
    root = payload.get("NBA_Player_Movement", {}) if isinstance(payload, dict) else {}
    rows = root.get("rows", [])
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    columns = []
    for column in root.get("columns", root.get("headers", [])):
        columns.append(column if isinstance(column, str) else column.get("name", column.get("Name", "")))
    return [dict(zip(columns, row)) for row in rows if isinstance(row, list)]


@dataclass
class CoreContext:
    active_teams: dict[str, set[int]]
    names_by_id: dict[str, set[str]]
    ids_by_name: dict[str, set[str]]
    affiliations: dict[tuple[str, str], set[int]]


def load_core_context() -> CoreContext:
    active_teams: dict[str, set[int]] = defaultdict(set)
    names_by_id: dict[str, set[str]] = defaultdict(set)
    ids_by_name: dict[str, set[str]] = defaultdict(set)
    affiliations: dict[tuple[str, str], set[int]] = defaultdict(set)

    if not CHECKPOINTS.exists():
        raise RuntimeError(f"Core checkpoints missing: {CHECKPOINTS}")

    for season_dir in sorted(path for path in CHECKPOINTS.iterdir() if path.is_dir() and re.match(r"^\d{4}-\d{2}$", path.name)):
        season = season_dir.name
        for path in sorted(season_dir.glob("*.json.gz")):
            try:
                team_id = int(path.name.split(".", 1)[0])
            except ValueError:
                continue
            active_teams[season].add(team_id)
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                continue
            for row in payload.get("player_totals", []):
                player_id = clean_id(row.get("EntityId") or row.get("RowId") or row.get("PlayerId"))
                player_name = str(row.get("Name") or row.get("ShortName") or "").strip()
                if not player_id or not player_name:
                    continue
                names_by_id[player_id].add(player_name)
                ids_by_name[normalize_name(player_name)].add(player_id)
                affiliations[(season, player_id)].add(team_id)

    return CoreContext(
        active_teams=dict(active_teams),
        names_by_id=dict(names_by_id),
        ids_by_name=dict(ids_by_name),
        affiliations=dict(affiliations),
    )


def resolve_team(name: str, season: str, context: CoreContext | None) -> tuple[int | None, str]:
    canonical = next((alias for alias in TEAM_ALIASES if alias.casefold() == name.strip().casefold()), None)
    if not canonical:
        return None, "unknown_team_name"
    candidates = list(TEAM_ALIASES[canonical])
    if len(candidates) == 1:
        return candidates[0], "unique_alias"
    if context is not None:
        active = context.active_teams.get(season, set())
        in_season = [team_id for team_id in candidates if team_id in active]
        if len(in_season) == 1:
            return in_season[0], "season_active_team_disambiguation"
    return None, "ambiguous_team_alias"


def adjacent_seasons(season: str) -> tuple[str, str]:
    year = int(season[:4])
    return season_label(year - 1), season_label(year + 1)


def resolve_player(
    player_name: str,
    season: str,
    context: CoreContext,
    source_team: int | None = None,
    destination_team: int | None = None,
) -> tuple[str | None, str]:
    candidates = sorted(context.ids_by_name.get(normalize_name(player_name), set()))
    if len(candidates) == 1:
        return candidates[0], "unique_core_name"
    if not candidates:
        return None, "not_in_core_identity"

    relevant_teams = {team for team in (source_team, destination_team) if team is not None}
    same_season = [
        player_id for player_id in candidates
        if context.affiliations.get((season, player_id), set()) & relevant_teams
    ]
    if len(same_season) == 1:
        return same_season[0], "same_season_team_context"

    previous, following = adjacent_seasons(season)
    adjacent = [
        player_id for player_id in candidates
        if (
            context.affiliations.get((previous, player_id), set())
            | context.affiliations.get((following, player_id), set())
        ) & relevant_teams
    ]
    if len(adjacent) == 1:
        return adjacent[0], "adjacent_season_team_context"
    return None, "ambiguous_core_name"


def bref_player_links(row: dict[str, Any]) -> list[dict[str, str]]:
    output = []
    for link in row.get("links", []):
        href = str(link.get("href") or "")
        text = str(link.get("text") or "").strip()
        if text and "/players/" in href:
            output.append({"text": text, "href": href, "title": str(link.get("title") or "")})
    return output


def players_in_text(text: str, row: dict[str, Any]) -> list[dict[str, str]]:
    found = []
    folded = normalize_name(text)
    for link in bref_player_links(row):
        name = link["text"]
        if normalize_name(name) and normalize_name(name) in folded:
            found.append(link)
    # Preserve source order while removing duplicate links/names.
    seen = set()
    unique = []
    for link in found:
        key = (normalize_name(link["text"]), link["href"])
        if key not in seen:
            seen.add(key)
            unique.append(link)
    return unique


@dataclass
class TransactionEvent:
    exact_date: str
    season: str
    event_type: str
    player_id: str | None
    player_name: str
    source_player_ref: str | None
    source_team_id: int | None
    destination_team_id: int | None
    source_team_name: str | None
    destination_team_name: str | None
    source_system: str
    source_reference: str
    identity_resolution: str
    team_resolution: str
    raw_text: str
    confidence: str


def make_event(
    *,
    row: dict[str, Any],
    event_type: str,
    player: dict[str, str],
    context: CoreContext,
    source_team_name: str | None = None,
    destination_team_name: str | None = None,
) -> TransactionEvent:
    season = str(row.get("season"))
    source_team, source_method = (resolve_team(source_team_name, season, context) if source_team_name else (None, "not_applicable"))
    destination_team, destination_method = (
        resolve_team(destination_team_name, season, context) if destination_team_name else (None, "not_applicable")
    )
    player_id, identity_method = resolve_player(
        player["text"], season, context, source_team, destination_team
    )
    team_method = f"source={source_method};destination={destination_method}"
    unresolved_team = (source_team_name and source_team is None) or (destination_team_name and destination_team is None)
    confidence = "high" if player_id and not unresolved_team else "review"
    return TransactionEvent(
        exact_date=str(row.get("exact_date") or row.get("subsection") or ""),
        season=season,
        event_type=event_type,
        player_id=player_id,
        player_name=player["text"],
        source_player_ref=player.get("href"),
        source_team_id=source_team,
        destination_team_id=destination_team,
        source_team_name=source_team_name,
        destination_team_name=destination_team_name,
        source_system="Basketball-Reference via Internet Archive",
        source_reference=f"{row.get('source_url')}#row-{row.get('row_index')}",
        identity_resolution=identity_method,
        team_resolution=team_method,
        raw_text=" ".join(map(str, row.get("cells", [])[1:])),
        confidence=confidence,
    )


def parse_bref_row(row: dict[str, Any], context: CoreContext) -> list[TransactionEvent]:
    cells = row.get("cells", [])
    text = str(cells[1] if len(cells) > 1 else "")
    events: list[TransactionEvent] = []

    trade_matches = list(TRADE_CLAUSE_RE.finditer(text))
    if trade_matches:
        for match in trade_matches:
            src_name, dst_name = match.group("src"), match.group("dst")
            for player in players_in_text(match.group("assets"), row):
                events.append(
                    make_event(
                        row=row,
                        event_type="trade",
                        player=player,
                        context=context,
                        source_team_name=src_name,
                        destination_team_name=dst_name,
                    )
                )
            returns = match.group("returns") or ""
            for player in players_in_text(returns, row):
                events.append(
                    make_event(
                        row=row,
                        event_type="trade",
                        player=player,
                        context=context,
                        source_team_name=dst_name,
                        destination_team_name=src_name,
                    )
                )
        return events

    for verb, regex in TEAM_VERB_RE.items():
        match = regex.search(text)
        if not match:
            continue
        team_name = match.group("team")
        body = match.group("body")
        players = players_in_text(body, row)
        for player in players:
            if verb in {"signed", "claimed"}:
                events.append(
                    make_event(
                        row=row,
                        event_type="acquire" if verb == "signed" else "claim",
                        player=player,
                        context=context,
                        destination_team_name=team_name,
                    )
                )
            else:
                events.append(
                    make_event(
                        row=row,
                        event_type="depart",
                        player=player,
                        context=context,
                        source_team_name=team_name,
                    )
                )
        return events
    return events


def read_bref_rows() -> Iterable[dict[str, Any]]:
    for path in sorted(BREF.glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def fetch_official_feed() -> tuple[list[dict[str, Any]], str]:
    errors = []
    for url in MOVEMENT_URLS:
        try:
            response = requests.get(url, headers=HEADERS, timeout=(10, 45))
            response.raise_for_status()
            payload = response.json()
            rows = movement_rows(payload)
            if not rows:
                raise RuntimeError("zero movement rows")
            OUT.mkdir(parents=True, exist_ok=True)
            with gzip.open(OFFICIAL_RAW, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            return rows, url
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("Official NBA movement feed unavailable: " + "; ".join(errors))


def team_name_from_description(description: str, *, exclude_team_id: int | None, season: str, context: CoreContext) -> tuple[str | None, int | None]:
    found: list[tuple[str, int]] = []
    for name in TEAM_ALIASES:
        if name.casefold() in description.casefold():
            team_id, _ = resolve_team(name, season, context)
            if team_id is not None and team_id != exclude_team_id:
                found.append((name, team_id))
    unique = []
    seen = set()
    for item in found:
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)
    return unique[0] if len(unique) == 1 else (None, None)


def parse_official_rows(rows: list[dict[str, Any]], source_url: str, context: CoreContext) -> list[TransactionEvent]:
    events: list[TransactionEvent] = []
    for index, row in enumerate(rows):
        player_id = clean_id(ci(row, "PLAYER_ID", "PlayerId"))
        if not player_id:
            continue
        transaction_type = str(ci(row, "Transaction_Type", "TRANSACTION_TYPE") or "").strip()
        if transaction_type == "ContractConverted":
            continue
        exact_date = str(ci(row, "TRANSACTION_DATE", "Date") or "").strip()
        description = str(ci(row, "TRANSACTION_DESCRIPTION", "Description") or "").strip()
        player_name = str(ci(row, "PLAYER_SLUG", "PlayerSlug") or "").replace("-", " ").title()
        canonical_names = sorted(context.names_by_id.get(player_id, set()))
        if canonical_names:
            player_name = canonical_names[0]
        destination = None
        source = None
        event_type = "other"
        team_id_raw = clean_id(ci(row, "TEAM_ID", "TeamId"))
        team_id = int(team_id_raw) if team_id_raw else None
        season_year = int(exact_date[:4]) if re.match(r"^\d{4}-", exact_date) else 2015
        # July-Dec movement belongs to the season starting that year; Jan-Jun
        # belongs to the season that started the prior calendar year. This is
        # only a transaction-source label; the tenure builder uses actual game
        # dates and does not infer in-season status from this month rule.
        month = int(exact_date[5:7]) if re.match(r"^\d{4}-\d{2}-\d{2}$", exact_date) else 7
        start_year = season_year if month >= 7 else season_year - 1
        season = season_label(start_year)
        if transaction_type in {"Signing", "AwardOnWaivers"}:
            destination = team_id
            event_type = "claim" if transaction_type == "AwardOnWaivers" else "acquire"
        elif transaction_type == "Waive":
            source = team_id
            event_type = "depart"
        elif transaction_type == "Trade":
            destination = team_id
            _, parsed_source = team_name_from_description(
                description, exclude_team_id=destination, season=season, context=context
            )
            source = parsed_source
            event_type = "trade"
        else:
            continue
        events.append(
            TransactionEvent(
                exact_date=exact_date,
                season=season,
                event_type=event_type,
                player_id=player_id,
                player_name=player_name,
                source_player_ref=str(ci(row, "PLAYER_SLUG", "PlayerSlug") or "") or None,
                source_team_id=source,
                destination_team_id=destination,
                source_team_name=None,
                destination_team_name=None,
                source_system="Official NBA Player Movement feed",
                source_reference=f"{source_url}#row-{index}",
                identity_resolution="official_nba_player_id",
                team_resolution="official_team_id" if team_id is not None else "review",
                raw_text=description,
                confidence="high" if team_id is not None else "review",
            )
        )
    return events


def write_events(events: list[TransactionEvent]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    events = sorted(events, key=lambda event: (event.exact_date, event.player_id or "", event.event_type, event.source_reference))
    with gzip.open(NORMALIZED, "wt", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def fake_context() -> CoreContext:
    return CoreContext(
        active_teams={"2001-02": set(sum((list(v) for v in TEAM_ALIASES.values()), [])), "2003-04": set(sum((list(v) for v in TEAM_ALIASES.values()), []))},
        names_by_id={},
        ids_by_name={},
        affiliations={},
    )


def self_test() -> None:
    context = fake_context()
    # The parser test deliberately supplies BRef-style player links; identity is
    # expected to remain unresolved because this synthetic context has no core IDs.
    samples = [
        {
            "season": "2001-02",
            "exact_date": "July 18, 2001",
            "row_index": 1,
            "source_url": "fixture",
            "cells": ["July 18, 2001", "The New Jersey Nets traded Stephon Marbury, Johnny Newman and Soumaila Samake to the Phoenix Suns for Chris Dudley and Jason Kidd."],
            "links": [
                {"text": name, "href": f"/players/x/{i}.html"}
                for i, name in enumerate(["Stephon Marbury", "Johnny Newman", "Soumaila Samake", "Chris Dudley", "Jason Kidd"])
            ],
        },
        {
            "season": "2003-04",
            "exact_date": "February 19, 2004",
            "row_index": 2,
            "source_url": "fixture",
            "cells": ["February 19, 2004", "In a 3-team trade, the Atlanta Hawks traded Rasheed Wallace to the Detroit Pistons; the Boston Celtics traded Chris Mills to the Atlanta Hawks; the Boston Celtics traded Mike James to the Detroit Pistons; the Detroit Pistons traded Željko Rebrača, Bob Sura and a 2004 1st round draft pick to the Atlanta Hawks; and the Detroit Pistons traded Chucky Atkins and Lindsey Hunter to the Boston Celtics."],
            "links": [
                {"text": name, "href": f"/players/y/{i}.html"}
                for i, name in enumerate(["Rasheed Wallace", "Chris Mills", "Mike James", "Željko Rebrača", "Bob Sura", "Chucky Atkins", "Lindsey Hunter"])
            ],
        },
        {
            "season": "2001-02",
            "exact_date": "January 7, 2002",
            "row_index": 3,
            "source_url": "fixture",
            "cells": ["January 7, 2002", "The Boston Celtics signed Example Player."],
            "links": [{"text": "Example Player", "href": "/players/e/example01.html"}],
        },
    ]
    first = parse_bref_row(samples[0], context)
    assert len(first) == 5, [asdict(event) for event in first]
    assert sum(event.destination_team_id == 1610612756 for event in first) == 3
    assert sum(event.destination_team_id == 1610612751 for event in first) == 2
    second = parse_bref_row(samples[1], context)
    assert len(second) == 7, [asdict(event) for event in second]
    assert any(event.player_name == "Rasheed Wallace" and event.destination_team_id == 1610612765 for event in second)
    third = parse_bref_row(samples[2], context)
    assert len(third) == 1 and third[0].event_type == "acquire"
    print("NORMALIZER SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--historical-only", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    context = load_core_context()
    events: list[TransactionEvent] = []
    row_count = 0
    empty_transaction_rows = []
    for row in read_bref_rows():
        row_count += 1
        parsed = parse_bref_row(row, context)
        events.extend(parsed)
        section = str(row.get("section") or "")
        if section in {"Trades", "Signings", "Waived", "Released", "Claimed"} and not parsed:
            empty_transaction_rows.append(
                {
                    "season": row.get("season"),
                    "exact_date": row.get("exact_date"),
                    "section": section,
                    "row_index": row.get("row_index"),
                    "cells": row.get("cells"),
                    "source_url": row.get("source_url"),
                }
            )

    official_count = 0
    official_source = None
    if not args.historical_only:
        official_rows, official_source = fetch_official_feed()
        official_events = parse_official_rows(official_rows, official_source, context)
        official_count = len(official_events)
        events.extend(official_events)

    unresolved_events = [asdict(event) for event in events if event.confidence != "high"]
    write_events(events)
    OUT.mkdir(parents=True, exist_ok=True)
    unresolved_report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "unresolved_event_count": len(unresolved_events),
        "unparsed_roster_transaction_row_count": len(empty_transaction_rows),
        "unresolved_events": unresolved_events,
        "unparsed_roster_transaction_rows": empty_transaction_rows,
    }
    UNRESOLVED.write_text(json.dumps(unresolved_report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "historical_source_rows": row_count,
        "normalized_events": len(events),
        "official_normalized_events": official_count,
        "official_source": official_source,
        "high_confidence_events": sum(event.confidence == "high" for event in events),
        "review_events": len(unresolved_events),
        "unparsed_roster_transaction_rows": len(empty_transaction_rows),
        "output": str(NORMALIZED),
        "unresolved_report": str(UNRESOLVED),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
