from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import date, timedelta, timezone, datetime
from pathlib import Path
from typing import Any

import normalize_roster_transactions as norm
from build_roster_tenure_windows import SEASON_BOUNDS

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
OFFICIAL_RAW = ROOT / "official_movement_feed.json.gz"
GAMES = ROOT / "regular_season_games.jsonl.gz"
SUMMARY = ROOT / "official_movement_boundary_repair_summary.json"
OFFICIAL_SOURCE = "Official NBA Player Movement feed"


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (
        str(r.get("exact_date") or ""), str(r.get("player_id") or ""),
        str(r.get("event_type") or ""), str(r.get("source_reference") or ""),
    ))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def canonical_day(value: Any) -> str:
    text = str(value or "").strip()
    return date.fromisoformat(text[:10]).isoformat()


def season_for_day(day: str) -> str:
    d = date.fromisoformat(day)
    year = d.year if d.month >= 7 else d.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def team_game_index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[str]]:
    out: dict[tuple[str, int], list[str]] = defaultdict(list)
    for game in rows:
        season = str(game.get("season") or "")
        day = str(game.get("game_date") or "")
        for field in ("home_team_id", "away_team_id"):
            team = int(game[field])
            out[(season, team)].append(day)
    for key in out:
        out[key] = sorted(set(out[key]))
    return dict(out)


def ten_day_expiry(signing_day: str, season: str, team: int, game_index: dict[tuple[str, int], list[str]]) -> str:
    d = date.fromisoformat(signing_day)
    calendar_end = (d + timedelta(days=10)).isoformat()
    later_games = [day for day in game_index.get((season, team), []) if day > signing_day]
    third_game = later_games[2] if len(later_games) >= 3 else None
    end = max([x for x in (calendar_end, third_game) if x])
    season_end = SEASON_BOUNDS[season][1]
    return min(end, season_end)


def raw_official_rows() -> list[dict[str, Any]]:
    if not OFFICIAL_RAW.exists():
        raise RuntimeError(f"Missing official movement cache: {OFFICIAL_RAW}")
    with gzip.open(OFFICIAL_RAW, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return norm.movement_rows(payload)


def repair_trade_sources(events: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> int:
    source_map: dict[tuple[str, str, int], int] = {}
    for row in raw_rows:
        if str(norm.ci(row, "Transaction_Type", "TRANSACTION_TYPE") or "") != "Trade":
            continue
        pid = norm.clean_id(norm.ci(row, "PLAYER_ID", "PlayerId"))
        team_id = norm.clean_id(norm.ci(row, "TEAM_ID", "TeamId"))
        source_id = norm.clean_id(norm.ci(row, "Additional_Sort", "ADDITIONAL_SORT"))
        raw_day = norm.ci(row, "TRANSACTION_DATE", "Date")
        if not pid or not team_id or not source_id or not raw_day:
            continue
        source_map[(canonical_day(raw_day), pid, int(team_id))] = int(source_id)

    repaired = 0
    for event in events:
        if event.get("source_system") != OFFICIAL_SOURCE or event.get("event_type") != "trade":
            continue
        try:
            key = (canonical_day(event.get("exact_date")), str(event.get("player_id")), int(event.get("destination_team_id") or 0))
        except Exception:
            continue
        source = source_map.get(key)
        if source and int(event.get("source_team_id") or 0) != source:
            event["source_team_id"] = source
            event["team_resolution"] = "official_team_id+Additional_Sort_source_team_id"
            event["trade_source_repaired_from_additional_sort"] = True
            repaired += 1
    return repaired


def add_ten_day_departures(events: list[dict[str, Any]], game_index: dict[tuple[str, int], list[str]]) -> tuple[int, int, int]:
    by_player_team: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        if season not in SEASON_BOUNDS or not pid:
            continue
        for team in {e.get("source_team_id"), e.get("destination_team_id")} - {None}:
            by_player_team[(season, pid, int(team))].append(e)

    existing_keys = {
        (
            str(e.get("exact_date") or ""), str(e.get("season") or ""),
            str(e.get("event_type") or ""), str(e.get("player_id") or ""),
            int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
            str(e.get("derived_boundary_type") or ""),
        )
        for e in events
    }

    added = 0
    skipped_explicit_departure = 0
    skipped_early_conversion = 0
    new_rows: list[dict[str, Any]] = []

    signings = [
        e for e in events
        if e.get("source_system") == OFFICIAL_SOURCE
        and e.get("event_type") == "acquire"
        and e.get("destination_team_id")
        and "10-day contract" in str(e.get("raw_text") or "").casefold()
        and not e.get("derived_boundary_type")
    ]

    for signing in signings:
        season = str(signing.get("season") or "")
        if season not in SEASON_BOUNDS:
            continue
        pid = str(signing.get("player_id") or "")
        team = int(signing.get("destination_team_id"))
        day = canonical_day(signing.get("exact_date"))
        ss, se = SEASON_BOUNDS[season]
        if not (ss <= day <= se):
            continue
        expiry = ten_day_expiry(day, season, team, game_index)
        peers = by_player_team.get((season, pid, team), [])

        explicit_out = [
            e for e in peers
            if e is not signing
            and e.get("event_type") in {"trade", "depart"}
            and e.get("source_team_id") == team
            and not e.get("derived_boundary_type")
            and day < str(e.get("exact_date") or "") <= expiry
        ]
        if explicit_out:
            skipped_explicit_departure += 1
            continue

        later_in = sorted(
            [
                e for e in peers
                if e is not signing
                and e.get("event_type") in {"trade", "acquire", "claim"}
                and e.get("destination_team_id") == team
                and day < str(e.get("exact_date") or "") < expiry
            ],
            key=lambda e: str(e.get("exact_date") or ""),
        )
        # A conversion/re-sign before the natural expiry supersedes the old
        # 10-day endpoint. A same-day renewal at the natural expiry is allowed
        # and is handled as continuous service by the chronology rebuilder.
        if later_in:
            skipped_early_conversion += 1
            continue

        row = {
            "exact_date": expiry,
            "season": season,
            "event_type": "depart",
            "player_id": pid,
            "player_name": signing.get("player_name") or pid,
            "source_player_ref": signing.get("source_player_ref"),
            "source_team_id": team,
            "destination_team_id": None,
            "source_team_name": signing.get("destination_team_name"),
            "destination_team_name": None,
            "source_system": OFFICIAL_SOURCE,
            "source_reference": f"{signing.get('source_reference') or 'official'}#derived-10day-expiry",
            "identity_resolution": "official_nba_player_id",
            "team_resolution": "official_team_id+deterministic_10day_expiry",
            "raw_text": (
                f"Derived natural expiration of 10-Day Contract signed {day}; "
                "endpoint is 10 calendar days after signing or the third scheduled regular-season game, whichever is later."
            ),
            "confidence": "high",
            "derived_boundary_type": "10_day_contract_natural_expiry",
            "derived_from_signing_date": day,
        }
        key = (
            row["exact_date"], row["season"], row["event_type"], row["player_id"],
            row["source_team_id"], 0, row["derived_boundary_type"],
        )
        if key not in existing_keys:
            existing_keys.add(key)
            new_rows.append(row)
            by_player_team[(season, pid, team)].append(row)
            added += 1

    events.extend(new_rows)
    return added, skipped_explicit_departure, skipped_early_conversion


def self_test() -> None:
    games = {
        ("2025-26", 10): ["2026-02-07", "2026-02-09", "2026-02-20"],
        ("2025-26", 20): ["2026-02-07", "2026-02-09", "2026-02-12"],
    }
    assert ten_day_expiry("2026-02-06", "2025-26", 10, games) == "2026-02-20"
    assert ten_day_expiry("2026-02-06", "2025-26", 20, games) == "2026-02-16"
    print("OFFICIAL MOVEMENT BOUNDARY REPAIR SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    if not EVENTS.exists() or not GAMES.exists():
        raise RuntimeError("Normalized transactions and regular-season schedule cache are required")

    events = read_jsonl_gz(EVENTS)
    raw_rows = raw_official_rows()
    game_index = team_game_index(read_jsonl_gz(GAMES))
    trade_repairs = repair_trade_sources(events, raw_rows)
    added_10day, skipped_out, skipped_conversion = add_ten_day_departures(events, game_index)
    write_jsonl_gz(EVENTS, events)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_output_events": len(events),
        "trade_source_team_ids_repaired_from_official_additional_sort": trade_repairs,
        "derived_10_day_departure_events_added": added_10day,
        "ten_day_signings_with_explicit_departure_before_expiry": skipped_out,
        "ten_day_signings_superseded_by_early_same_team_acquisition": skipped_conversion,
        "policy": (
            "Trade source team IDs use the official NBA movement Additional_Sort field. "
            "A 10-Day Contract receives a deterministic natural roster endpoint of 10 calendar days "
            "after the signing date or the third scheduled regular-season team game, whichever is later; "
            "an explicit earlier departure or contract conversion takes precedence."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
