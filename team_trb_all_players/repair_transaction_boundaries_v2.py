from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import date, timedelta, timezone, datetime
from pathlib import Path
from typing import Any

from build_roster_tenure_windows import SEASON_BOUNDS

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
GAMES = ROOT / "regular_season_games.jsonl.gz"
SUMMARY = ROOT / "transaction_boundary_repair_v2_summary.json"
OFFICIAL_SOURCE = "Official NBA Player Movement feed"
DERIVED = "10_day_contract_natural_expiry"


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


def canonical_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except Exception:
        return None


def team_game_index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[str]]:
    out: dict[tuple[str, int], list[str]] = defaultdict(list)
    for game in rows:
        season = str(game.get("season") or "")
        day = str(game.get("game_date") or "")
        for field in ("home_team_id", "away_team_id"):
            out[(season, int(game[field]))].append(day)
    for key in out:
        out[key] = sorted(set(out[key]))
    return dict(out)


def ten_day_expiry(signing_day: str, season: str, team: int, game_index: dict[tuple[str, int], list[str]]) -> str:
    """Return the last day of a 10-day deal under the 10-days-or-3-games rule.

    Ten calendar days inclusive means a deal signed on Feb 19 reaches its tenth
    calendar day on Feb 28 (signing day + 9). Team games on the signing date can
    count toward the three-game limb, so scheduled games are counted >= signing.
    """
    d = date.fromisoformat(signing_day)
    tenth_calendar_day = (d + timedelta(days=9)).isoformat()
    eligible_games = [day for day in game_index.get((season, team), []) if day >= signing_day]
    third_game = eligible_games[2] if len(eligible_games) >= 3 else None
    candidates = [tenth_calendar_day]
    if third_game:
        candidates.append(third_game)
    end = max(candidates)
    return min(end, SEASON_BOUNDS[season][1])


def remove_superseded_official_trades(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove an earlier official trade contradicted by later official source-team evidence.

    If the official movement feed says player P moved A->B, then later (same
    season) still identifies A as P's source/departing team, the earlier A->B
    transaction cannot have established an uninterrupted B tenure unless P was
    reacquired by A in between. With no intervening official acquisition into A,
    the earlier trade is treated as superseded/voided transaction history.
    Same-day multi-team chains are intentionally not removed here.
    """
    by_player_season: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("source_system") != OFFICIAL_SOURCE:
            continue
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        day = canonical_day(e.get("exact_date"))
        if season in SEASON_BOUNDS and pid and day:
            e["_repair_day"] = day
            by_player_season[(season, pid)].append(e)

    remove_ids: set[int] = set()
    audit: list[dict[str, Any]] = []
    for (season, pid), rows in by_player_season.items():
        rows.sort(key=lambda e: (e["_repair_day"], str(e.get("source_reference") or "")))
        for i, e in enumerate(rows):
            if e.get("event_type") != "trade":
                continue
            src = int(e.get("source_team_id") or 0)
            dst = int(e.get("destination_team_id") or 0)
            if not src or not dst:
                continue
            day = e["_repair_day"]
            reacquired = False
            contradiction: dict[str, Any] | None = None
            for later in rows[i + 1:]:
                later_day = later["_repair_day"]
                if later_day <= day:
                    continue
                if int(later.get("destination_team_id") or 0) == src and later.get("event_type") in {"trade", "acquire", "claim"}:
                    reacquired = True
                    break
                if int(later.get("source_team_id") or 0) == src and later.get("event_type") in {"trade", "depart"}:
                    contradiction = later
                    break
            if contradiction is not None and not reacquired:
                remove_ids.add(id(e))
                audit.append({
                    "season": season,
                    "player_id": pid,
                    "removed_date": day,
                    "removed_source_team_id": src,
                    "removed_destination_team_id": dst,
                    "removed_raw_text": e.get("raw_text"),
                    "contradicting_later_date": contradiction.get("_repair_day"),
                    "contradicting_event_type": contradiction.get("event_type"),
                    "contradicting_source_team_id": contradiction.get("source_team_id"),
                    "contradicting_destination_team_id": contradiction.get("destination_team_id"),
                    "contradicting_raw_text": contradiction.get("raw_text"),
                })

    cleaned = []
    for e in events:
        e.pop("_repair_day", None)
        if id(e) not in remove_ids:
            cleaned.append(e)
    return cleaned, audit


def add_all_source_ten_day_departures(events: list[dict[str, Any]], game_index: dict[tuple[str, int], list[str]]) -> dict[str, int]:
    # Remove any previously derived rows so V2 always recomputes with the
    # inclusive +9-day rule and can never accumulate stale V1 endpoints.
    events[:] = [e for e in events if e.get("derived_boundary_type") != DERIVED]

    by_player_team: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        if season not in SEASON_BOUNDS or not pid:
            continue
        for team in {e.get("source_team_id"), e.get("destination_team_id")} - {None}:
            by_player_team[(season, pid, int(team))].append(e)

    added = 0
    official_added = 0
    historical_added = 0
    skipped_explicit = 0
    skipped_early_conversion = 0
    skipped_bad = 0
    new_rows: list[dict[str, Any]] = []

    signings = [
        e for e in events
        if e.get("event_type") in {"acquire", "claim"}
        and e.get("destination_team_id")
        and "10-day contract" in str(e.get("raw_text") or "").casefold()
    ]

    for signing in signings:
        season = str(signing.get("season") or "")
        pid = str(signing.get("player_id") or "")
        team = int(signing.get("destination_team_id") or 0)
        day = canonical_day(signing.get("exact_date"))
        if season not in SEASON_BOUNDS or not pid or not team or not day:
            skipped_bad += 1
            continue
        ss, se = SEASON_BOUNDS[season]
        if not (ss <= day <= se):
            continue
        expiry = ten_day_expiry(day, season, team, game_index)
        peers = by_player_team.get((season, pid, team), [])

        explicit_out = [
            e for e in peers
            if e is not signing
            and e.get("event_type") in {"trade", "depart"}
            and int(e.get("source_team_id") or 0) == team
            and e.get("derived_boundary_type") != DERIVED
            and day < str(canonical_day(e.get("exact_date")) or "") <= expiry
        ]
        if explicit_out:
            skipped_explicit += 1
            continue

        later_same_team_in = [
            e for e in peers
            if e is not signing
            and e.get("event_type") in {"trade", "acquire", "claim"}
            and int(e.get("destination_team_id") or 0) == team
            and day < str(canonical_day(e.get("exact_date")) or "") < expiry
        ]
        if later_same_team_in:
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
            "source_system": signing.get("source_system") or "derived from transaction source",
            "source_reference": f"{signing.get('source_reference') or 'transaction'}#derived-10day-expiry-v2",
            "identity_resolution": signing.get("identity_resolution") or "inherited_from_signing",
            "team_resolution": "deterministic_10day_expiry_v2",
            "raw_text": (
                f"Derived natural expiration of 10-Day Contract signed {day}; endpoint is the tenth "
                "calendar day inclusive or the third scheduled regular-season team game, whichever is later."
            ),
            "confidence": "high",
            "derived_boundary_type": DERIVED,
            "derived_from_signing_date": day,
            "derived_from_source_system": signing.get("source_system"),
        }
        new_rows.append(row)
        by_player_team[(season, pid, team)].append(row)
        added += 1
        if signing.get("source_system") == OFFICIAL_SOURCE:
            official_added += 1
        else:
            historical_added += 1

    events.extend(new_rows)
    return {
        "derived_10_day_departure_events_added": added,
        "official_10_day_departures_added": official_added,
        "historical_or_supplementary_10_day_departures_added": historical_added,
        "ten_day_signings_with_explicit_departure_before_expiry": skipped_explicit,
        "ten_day_signings_superseded_by_early_same_team_acquisition": skipped_early_conversion,
        "ten_day_signings_skipped_bad_metadata": skipped_bad,
    }


def self_test() -> None:
    games = {
        ("2025-26", 10): ["2026-02-06", "2026-02-09", "2026-02-20"],
        ("2025-26", 20): ["2026-02-07", "2026-02-09", "2026-02-12"],
    }
    assert ten_day_expiry("2026-02-06", "2025-26", 10, games) == "2026-02-20"
    assert ten_day_expiry("2026-02-06", "2025-26", 20, games) == "2026-02-15"

    sample = [
        {"season": "2021-22", "player_id": "1", "event_type": "trade", "exact_date": "2022-01-10", "source_team_id": 10, "destination_team_id": 20, "source_system": OFFICIAL_SOURCE, "raw_text": "A to B"},
        {"season": "2021-22", "player_id": "1", "event_type": "trade", "exact_date": "2022-01-19", "source_team_id": 10, "destination_team_id": 30, "source_system": OFFICIAL_SOURCE, "raw_text": "A to C"},
    ]
    cleaned, audit = remove_superseded_official_trades(sample)
    assert len(cleaned) == 1 and cleaned[0]["destination_team_id"] == 30 and len(audit) == 1
    print("TRANSACTION BOUNDARY REPAIR V2 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not EVENTS.exists() or not GAMES.exists():
        raise RuntimeError("Normalized transactions and cached regular-season schedules are required")

    events = read_jsonl_gz(EVENTS)
    before = len(events)
    events, superseded = remove_superseded_official_trades(events)
    games = team_game_index(read_jsonl_gz(GAMES))
    ten_day_stats = add_all_source_ten_day_departures(events, games)
    write_jsonl_gz(EVENTS, events)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_events": before,
        "output_events": len(events),
        "superseded_official_trade_events_removed": len(superseded),
        "superseded_trade_audit": superseded,
        **ten_day_stats,
        "policy": (
            "An earlier official trade is removed only when later same-season official movement still "
            "identifies the same source team as the player's departing team and there is no intervening "
            "official reacquisition into that source team. 10-day endpoints use ten calendar days inclusive "
            "(signing day + 9) or the third scheduled team game on/after signing, whichever is later."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
