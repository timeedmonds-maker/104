from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from build_roster_tenure_windows import SEASON_BOUNDS

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
GAMES = ROOT / "regular_season_games.jsonl.gz"
TARGETS = ROOT / "remaining_overlap_event_chains_v11.json"
SUMMARY = ROOT / "overlap_boundary_repair_v12_summary.json"
OFFICIAL = "Official NBA Player Movement feed"
OFFICIAL_10DAY = "10_day_contract_natural_expiry"
HIST_V11 = "historical_single_10_day_contract_natural_expiry_v11"
HIST_V12 = "historical_10_day_contract_natural_expiry_v12"
CURATED = "V12 verified transaction supplement"


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


def parse_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except Exception:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def target_keys() -> set[tuple[str, str]]:
    data = json.loads(TARGETS.read_text(encoding="utf-8"))
    return {(str(c["season"]), str(c["player_id"])) for c in data.get("cases") or []}


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


def ten_day_end(signing_day: str, season: str, team: int, games: dict[tuple[str, int], list[str]]) -> str:
    tenth_day = (date.fromisoformat(signing_day) + timedelta(days=9)).isoformat()
    later_games = [d for d in games.get((season, team), []) if d > signing_day]
    third_game = later_games[2] if len(later_games) >= 3 else None
    end = max([x for x in (tenth_day, third_game) if x])
    return min(end, SEASON_BOUNDS[season][1])


def dedupe_key(e: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(e.get("season") or ""), str(e.get("player_id") or ""),
        str(e.get("event_type") or ""), str(parse_day(e.get("exact_date")) or e.get("exact_date") or ""),
        int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
    )


def curated_event(
    season: str, pid: str, name: str, day: str, event_type: str,
    source: int | None, destination: int | None, reference: str, note: str,
) -> dict[str, Any]:
    return {
        "exact_date": day,
        "season": season,
        "event_type": event_type,
        "player_id": pid,
        "player_name": name,
        "source_player_ref": None,
        "source_team_id": source,
        "destination_team_id": destination,
        "source_team_name": None,
        "destination_team_name": None,
        "source_system": CURATED,
        "source_reference": reference,
        "identity_resolution": "verified_player_id_v12",
        "team_resolution": "verified_team_transaction_v12",
        "raw_text": note,
        "confidence": "high",
        "verified_boundary_v12": True,
    }


def verified_supplement() -> list[dict[str, Any]]:
    """Small exact-date supplement for cases whose cached feeds omit the decisive boundary."""
    rows: list[dict[str, Any]] = []
    # Garrett Temple: Milwaukee first/second 10-day sequence.
    ref = "https://basketball.realgm.com/nba/teams/Milwaukee-Bucks/16/Transaction_History/2011"
    rows += [
        curated_event("2010-11", "202066", "Garrett Temple", "2011-02-04", "depart", 1610612749, None, ref, "Milwaukee first 10-day contract ended."),
        curated_event("2010-11", "202066", "Garrett Temple", "2011-02-05", "acquire", None, 1610612749, ref, "Milwaukee second 10-day contract began."),
        curated_event("2010-11", "202066", "Garrett Temple", "2011-02-15", "depart", 1610612749, None, ref, "Milwaukee second 10-day contract ended."),
    ]
    # Jarron Collins: Clippers and Portland first/second 10-day sequences.
    lac = "https://basketball.realgm.com/nba/teams/Los-Angeles-Clippers/12/Transaction_History/2011"
    por = "https://basketball.realgm.com/nba/teams/Portland-Trail-Blazers/24/Transaction_History/2011"
    rows += [
        curated_event("2010-11", "2260", "Jarron Collins", "2011-01-15", "depart", 1610612746, None, lac, "Clippers first 10-day contract ended."),
        curated_event("2010-11", "2260", "Jarron Collins", "2011-01-15", "acquire", None, 1610612746, lac, "Clippers second 10-day contract began."),
        curated_event("2010-11", "2260", "Jarron Collins", "2011-01-25", "depart", 1610612746, None, lac, "Clippers second 10-day contract ended."),
        curated_event("2010-11", "2260", "Jarron Collins", "2011-03-11", "depart", 1610612757, None, por, "Portland first 10-day contract ended."),
        curated_event("2010-11", "2260", "Jarron Collins", "2011-03-11", "acquire", None, 1610612757, por, "Portland second 10-day contract began."),
        curated_event("2010-11", "2260", "Jarron Collins", "2011-03-21", "depart", 1610612757, None, por, "Portland second 10-day contract ended."),
    ]
    # Shelvin Mack: Philadelphia two 10-day contracts.
    phi = "https://basketball.realgm.com/nba/teams/Philadelphia-Sixers/22/Transaction_History/2013"
    rows += [
        curated_event("2012-13", "202714", "Shelvin Mack", "2013-01-27", "depart", 1610612755, None, phi, "Philadelphia first 10-day contract ended."),
        curated_event("2012-13", "202714", "Shelvin Mack", "2013-01-28", "acquire", None, 1610612755, phi, "Philadelphia second 10-day contract began."),
        curated_event("2012-13", "202714", "Shelvin Mack", "2013-02-07", "depart", 1610612755, None, phi, "Philadelphia second 10-day contract ended."),
    ]
    # Jannero Pargo: Atlanta two 10-day contracts.
    atl = "https://basketball.realgm.com/nba/teams/Atlanta-Hawks/1/Transaction_History/2013"
    rows += [
        curated_event("2012-13", "2457", "Jannero Pargo", "2013-01-31", "depart", 1610612737, None, atl, "Atlanta first 10-day contract ended."),
        curated_event("2012-13", "2457", "Jannero Pargo", "2013-02-02", "acquire", None, 1610612737, atl, "Atlanta second 10-day contract began."),
        curated_event("2012-13", "2457", "Jannero Pargo", "2013-02-12", "depart", 1610612737, None, atl, "Atlanta second 10-day contract ended."),
    ]
    # James Ennis III: Nets deal expired after the Dec. 27 game before the Clippers signing.
    rows.append(curated_event(
        "2021-22", "203516", "James Ennis III", "2021-12-27", "depart", 1610612751, None,
        "https://www.latimes.com/sports/clippers/story/2021-12-30/james-ennis-iii-eager-capitalize-hometown-team-clippers-nba",
        "Brooklyn 10-day contract expired following the December 27 game.",
    ))
    # Modern trades missing/misrepresented in the movement normalization.
    rows += [
        curated_event("2024-25", "1630215", "Jared Butler", "2025-02-06", "trade", 1610612764, 1610612755,
                      "https://www.nba.com/sixers/news/philadelphia-76ers-acquire-jared-butler-four-draft-picks-from-washington-wizards",
                      "Philadelphia acquired Jared Butler from Washington."),
        curated_event("2024-25", "1641736", "Reece Beekman", "2024-12-15", "trade", 1610612744, 1610612751,
                      "https://www.nba.com/nets/news/brooklyn-nets-complete-trade-with-golden-state-warriors",
                      "Brooklyn acquired Reece Beekman from Golden State."),
        curated_event("2025-26", "1641801", "Emanuel Miller", "2026-02-01", "trade", 1610612741, 1610612739,
                      "https://www.nba.com/news/2025-26-nba-trade-tracker",
                      "Cleveland acquired Emanuel Miller from Chicago in the three-team trade."),
    ]
    return rows


def fix_2020_bubble_season(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit = []
    for e in events:
        day = parse_day(e.get("exact_date"))
        if e.get("source_system") != OFFICIAL or not day:
            continue
        if "2020-07-01" <= day <= "2020-08-14" and str(e.get("season") or "") != "2019-20":
            audit.append({"player_id": e.get("player_id"), "day": day, "old_season": e.get("season"), "event_type": e.get("event_type")})
            e["season"] = "2019-20"
            e["v12_bubble_season_reassigned"] = True
    return audit


def collapse_official_trade_duplicates(events: list[dict[str, Any]], targets: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """For targeted cases, keep the later finalized record among duplicate official trades."""
    groups: dict[tuple[str, str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        season, pid = str(e.get("season") or ""), str(e.get("player_id") or "")
        if (season, pid) not in targets or e.get("source_system") != OFFICIAL or e.get("event_type") != "trade":
            continue
        src, dst, day = int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0), parse_day(e.get("exact_date"))
        if src and dst and day:
            groups[(season, pid, src, dst)].append(e)

    remove_ids: set[int] = set()
    audit: list[dict[str, Any]] = []
    for key, rows in groups.items():
        rows.sort(key=lambda e: parse_day(e.get("exact_date")) or "")
        cluster: list[dict[str, Any]] = []
        for row in rows:
            if not cluster:
                cluster = [row]
                continue
            d0 = date.fromisoformat(parse_day(cluster[-1].get("exact_date")) or "1900-01-01")
            d1 = date.fromisoformat(parse_day(row.get("exact_date")) or "1900-01-01")
            if (d1 - d0).days <= 7:
                cluster.append(row)
            else:
                if len(cluster) > 1:
                    winner = cluster[-1]
                    for old in cluster[:-1]:
                        remove_ids.add(id(old))
                        audit.append({"key": key, "removed": parse_day(old.get("exact_date")), "kept": parse_day(winner.get("exact_date"))})
                cluster = [row]
        if len(cluster) > 1:
            winner = cluster[-1]
            for old in cluster[:-1]:
                remove_ids.add(id(old))
                audit.append({"key": key, "removed": parse_day(old.get("exact_date")), "kept": parse_day(winner.get("exact_date"))})
    return [e for e in events if id(e) not in remove_ids], audit


def collapse_conflicting_preliminary_trades(events: list[dict[str, Any]], targets: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_player: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        key = (str(e.get("season") or ""), str(e.get("player_id") or ""))
        if key in targets and e.get("source_system") == OFFICIAL and e.get("event_type") == "trade" and parse_day(e.get("exact_date")):
            by_player[key].append(e)
    remove_ids: set[int] = set()
    audit = []
    for key, rows in by_player.items():
        rows.sort(key=lambda e: parse_day(e.get("exact_date")) or "")
        for i, earlier in enumerate(rows):
            src = int(earlier.get("source_team_id") or 0)
            dst = int(earlier.get("destination_team_id") or 0)
            d0s = parse_day(earlier.get("exact_date"))
            if not src or not dst or not d0s:
                continue
            d0 = date.fromisoformat(d0s)
            for later in rows[i + 1:]:
                d1s = parse_day(later.get("exact_date")); later_src = int(later.get("source_team_id") or 0); later_dst = int(later.get("destination_team_id") or 0)
                if not d1s or (date.fromisoformat(d1s) - d0).days > 14:
                    break
                if later_src == src and later_dst and later_dst != dst:
                    remove_ids.add(id(earlier))
                    audit.append({"key": key, "removed": d0s, "removed_destination": dst, "kept": d1s, "kept_destination": later_dst})
                    break
    return [e for e in events if id(e) not in remove_ids], audit


def remove_voided_trades(events: list[dict[str, Any]], targets: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Suppress a trade when the same official source team later waives/releases the player without reacquisition."""
    by_player: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        key = (str(e.get("season") or ""), str(e.get("player_id") or ""))
        if key in targets and parse_day(e.get("exact_date")):
            by_player[key].append(e)
    remove_ids: set[int] = set(); audit = []
    mirrored: list[tuple[str, str, str, int]] = []
    for key, rows in by_player.items():
        rows.sort(key=lambda e: parse_day(e.get("exact_date")) or "")
        officials = [e for e in rows if e.get("source_system") == OFFICIAL]
        for i, trade in enumerate(officials):
            if trade.get("event_type") != "trade":
                continue
            src, dst = int(trade.get("source_team_id") or 0), int(trade.get("destination_team_id") or 0)
            d0s = parse_day(trade.get("exact_date"))
            if not src or not dst or not d0s:
                continue
            d0 = date.fromisoformat(d0s)
            reacquired = False
            for later in officials[i + 1:]:
                d1s = parse_day(later.get("exact_date"))
                if not d1s:
                    continue
                delta = (date.fromisoformat(d1s) - d0).days
                if delta > 90:
                    break
                if int(later.get("destination_team_id") or 0) == src and later.get("event_type") in {"trade", "acquire", "claim"}:
                    reacquired = True
                    break
                if int(later.get("source_team_id") or 0) == src and later.get("event_type") == "depart" and not reacquired:
                    remove_ids.add(id(trade))
                    mirrored.append((key[0], key[1], d0s, dst))
                    audit.append({"key": key, "voided_trade_date": d0s, "source_team": src, "destination_team": dst, "later_source_departure": d1s})
                    break
    # Remove mirrored supplementary records for the same voided move/date so they cannot recreate a phantom destination stint.
    for e in events:
        season, pid, day = str(e.get("season") or ""), str(e.get("player_id") or ""), parse_day(e.get("exact_date"))
        for s, p, d, dst in mirrored:
            if season == s and pid == p and day == d and e.get("event_type") == "trade" and int(e.get("destination_team_id") or 0) == dst:
                remove_ids.add(id(e))
    return [e for e in events if id(e) not in remove_ids], audit


def historical_10day_signing(e: dict[str, Any]) -> bool:
    if e.get("source_system") in {OFFICIAL, CURATED} or e.get("event_type") not in {"acquire", "claim"} or not e.get("destination_team_id"):
        return False
    raw = str(e.get("raw_text") or "").casefold()
    if "10-day contract" not in raw:
        return False
    if "two 10-day contracts" in raw or "then signed" in raw or "rest of the season" in raw or "first of two 10-day contracts" in raw:
        return False
    return True


def rebuild_historical_10day_endpoints(events: list[dict[str, Any]], games: dict[tuple[str, int], list[str]]) -> tuple[int, list[dict[str, Any]]]:
    # Remove v11/v12 inferred historical endpoints; exact source-stated expiries remain authoritative.
    events[:] = [e for e in events if e.get("derived_boundary_type") not in {HIST_V11, HIST_V12}]
    by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        season, pid = str(e.get("season") or ""), str(e.get("player_id") or "")
        if season not in SEASON_BOUNDS or not pid:
            continue
        for team0 in {e.get("source_team_id"), e.get("destination_team_id")} - {None}:
            by_key[(season, pid, int(team0))].append(e)

    additions = []; audit = []
    for signing in [e for e in events if historical_10day_signing(e)]:
        season, pid = str(signing.get("season") or ""), str(signing.get("player_id") or "")
        team = int(signing.get("destination_team_id") or 0); day = parse_day(signing.get("exact_date"))
        if season not in SEASON_BOUNDS or not pid or not team or not day:
            continue
        ss, se = SEASON_BOUNDS[season]
        if not (ss <= day <= se):
            continue
        natural = ten_day_end(day, season, team, games)
        peers = by_key[(season, pid, team)]

        # Any exact departure shortly after this signing outranks an inferred natural endpoint,
        # even if source scheduling makes the inferred endpoint a day or two earlier.
        explicit = []
        for p in peers:
            if p is signing or p.get("event_type") not in {"trade", "depart"} or int(p.get("source_team_id") or 0) != team:
                continue
            if p.get("derived_boundary_type") in {OFFICIAL_10DAY, HIST_V11, HIST_V12}:
                continue
            pd = parse_day(p.get("exact_date"))
            if pd and day < pd <= (date.fromisoformat(day) + timedelta(days=16)).isoformat():
                explicit.append(p)
        if explicit:
            continue

        # A same-team renewal/conversion before or just after the natural end keeps service continuous;
        # only the final explicit 10-day in the sequence needs a natural endpoint.
        later_in = []
        for p in peers:
            if p is signing or p.get("event_type") not in {"trade", "acquire", "claim"} or int(p.get("destination_team_id") or 0) != team:
                continue
            pd = parse_day(p.get("exact_date"))
            if pd and day < pd <= (date.fromisoformat(natural) + timedelta(days=2)).isoformat():
                later_in.append(p)
        if later_in:
            continue

        row = {
            "exact_date": natural, "season": season, "event_type": "depart",
            "player_id": pid, "player_name": signing.get("player_name") or pid,
            "source_player_ref": signing.get("source_player_ref"),
            "source_team_id": team, "destination_team_id": None,
            "source_team_name": signing.get("destination_team_name"), "destination_team_name": None,
            "source_system": signing.get("source_system") or "historical transaction source",
            "source_reference": f"{signing.get('source_reference') or 'historical'}#derived-10day-v12",
            "identity_resolution": signing.get("identity_resolution") or "inherited_from_signing",
            "team_resolution": "deterministic_historical_10day_expiry_v12",
            "raw_text": f"Derived natural endpoint from historical 10-day signing on {day}; exact source departure was not present.",
            "confidence": "high", "derived_boundary_type": HIST_V12, "derived_from_signing_date": day,
        }
        additions.append(row); by_key[(season, pid, team)].append(row)
        audit.append({"season": season, "player_id": pid, "team_id": team, "signing": day, "end": natural})
    events.extend(additions)
    return len(additions), audit


def apply_verified_supplement(events: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    # James Ennis: discard the schedule-derived Nets end because contemporaneous reporting explicitly states the deal expired after Dec 27.
    removed = []
    kept = []
    for e in events:
        if (
            str(e.get("season")) == "2021-22" and str(e.get("player_id")) == "203516"
            and int(e.get("source_team_id") or 0) == 1610612751
            and e.get("derived_boundary_type") == OFFICIAL_10DAY
            and str(e.get("derived_from_signing_date") or "")[:10] == "2021-12-18"
        ):
            removed.append(e)
            continue
        kept.append(e)
    events[:] = kept
    seen = {dedupe_key(e) for e in events}
    added = []
    for e in verified_supplement():
        key = dedupe_key(e)
        if key not in seen:
            seen.add(key); events.append(e); added.append(e)
    return len(added), removed


def self_test() -> None:
    games = {("2014-15", 10): ["2015-03-07", "2015-03-09", "2015-03-15"]}
    assert ten_day_end("2015-03-06", "2014-15", 10, games) == "2015-03-15"
    assert historical_10day_signing({"source_system": "Basketball-Reference", "event_type": "acquire", "destination_team_id": 10, "raw_text": "signed X to a 2nd 10-day contract."})
    assert not historical_10day_signing({"source_system": "Basketball-Reference", "event_type": "acquire", "destination_team_id": 10, "raw_text": "signed X to the first of two 10-day contracts."})
    assert any(e["player_id"] == "1641736" and e["event_type"] == "trade" for e in verified_supplement())
    print("OVERLAP BOUNDARY REPAIR V12 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if not EVENTS.exists() or not GAMES.exists() or not TARGETS.exists():
        raise RuntimeError("v11 event stream, schedules, and overlap diagnostic are required")
    events = read_jsonl_gz(EVENTS); before = len(events); targets = target_keys(); games = team_game_index(read_jsonl_gz(GAMES))

    bubble = fix_2020_bubble_season(events)
    events, same_move = collapse_official_trade_duplicates(events, targets)
    events, different_move = collapse_conflicting_preliminary_trades(events, targets)
    events, voided = remove_voided_trades(events, targets)
    hist_added, hist_audit = rebuild_historical_10day_endpoints(events, games)
    verified_added, verified_removed = apply_verified_supplement(events)

    # Final exact-key dedupe, preferring verified supplement then official then source-stated historical records.
    priority = {CURATED: 4, OFFICIAL: 3, "Basketball-Reference via Internet Archive": 2, "RealGM league transaction history": 1}
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for e in events:
        key = dedupe_key(e)
        old = best.get(key)
        score = priority.get(str(e.get("source_system") or ""), 0) + (2 if e.get("derived_boundary_type") == "source_stated_10_day_contract_expiry" else 0)
        oldscore = priority.get(str(old.get("source_system") or ""), 0) + (2 if old and old.get("derived_boundary_type") == "source_stated_10_day_contract_expiry" else 0)
        if old is None or score > oldscore:
            best[key] = e
    events = list(best.values())
    write_jsonl_gz(EVENTS, events)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_events": before, "output_events": len(events),
        "bubble_official_events_reassigned_to_2019_20": len(bubble),
        "duplicate_same_move_official_trades_removed": len(same_move),
        "conflicting_preliminary_official_trades_removed": len(different_move),
        "voided_trade_chains_removed": len(voided),
        "historical_10day_endpoints_rebuilt": hist_added,
        "verified_supplement_events_added": verified_added,
        "superseded_james_ennis_derived_endpoints_removed": len(verified_removed),
        "bubble_audit_sample": bubble[:100], "same_move_trade_audit": same_move,
        "different_move_trade_audit": different_move, "voided_trade_audit": voided,
        "historical_10day_audit_sample": hist_audit[:100],
        "policy": (
            "No QA gate is weakened. Targeted duplicate NBA movement records are collapsed to one finalized transaction record; "
            "voided trades are suppressed only when the original official source team later records the player's departure without reacquisition; "
            "historical 10-day endpoints defer to explicit source-stated expiries; and a small verified supplement supplies exact boundaries "
            "for source gaps documented in transaction histories/team releases. July-August 2020 movement rows are assigned to the still-active 2019-20 regular season."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_sample") and not k.endswith("_audit")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
