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
SUMMARY = ROOT / "remaining_overlap_source_repair_v11_summary.json"
OFFICIAL = "Official NBA Player Movement feed"
OFFICIAL_10DAY = "10_day_contract_natural_expiry"
HIST_10DAY = "historical_single_10_day_contract_natural_expiry_v11"


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


def deterministic_10day_end(signing_day: str, season: str, team: int, games: dict[tuple[str, int], list[str]]) -> str:
    # Under the TREB transaction-day convention an incoming affiliation becomes
    # effective the next calendar day. The contract's tenth calendar day is
    # therefore signing+9; only team games after the signing date are counted
    # for the three-game limb, avoiding unsupported intra-day assumptions.
    tenth_day = (date.fromisoformat(signing_day) + timedelta(days=9)).isoformat()
    later_games = [d for d in games.get((season, team), []) if d > signing_day]
    third_game = later_games[2] if len(later_games) >= 3 else None
    end = max([x for x in (tenth_day, third_game) if x])
    return min(end, SEASON_BOUNDS[season][1])


def correct_existing_official_10day_dates(events: list[dict[str, Any]], games: dict[tuple[str, int], list[str]]) -> tuple[int, list[dict[str, Any]]]:
    changed = 0
    audit: list[dict[str, Any]] = []
    for e in events:
        if e.get("derived_boundary_type") != OFFICIAL_10DAY:
            continue
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        team = int(e.get("source_team_id") or 0)
        signing = parse_day(e.get("derived_from_signing_date"))
        if season not in SEASON_BOUNDS or not pid or not team or not signing:
            continue
        corrected = deterministic_10day_end(signing, season, team, games)
        old = parse_day(e.get("exact_date"))
        if old != corrected:
            e["exact_date"] = corrected
            e["team_resolution"] = str(e.get("team_resolution") or "") + ";v11_inclusive_10day_endpoint"
            e["v11_corrected_10day_endpoint"] = True
            changed += 1
            audit.append({"season": season, "player_id": pid, "team_id": team, "signing": signing, "old_end": old, "new_end": corrected})
    return changed, audit


def remove_conflicting_official_preliminary_trades(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove only an earlier official trade contradicted by a later official trade.

    This deliberately does NOT use later waivers/releases as contradictions (the
    v9 regression did that and incorrectly removed valid completed trades). The
    rule is limited to: same player, same season, same source team, later official
    trade within 14 days, different destination, and no intervening reacquisition
    into the source team. This captures preliminary/voided reroutes such as Bol
    Bol DEN->DET before the later DEN->BOS transaction and Caris LeVert BKN->HOU
    before the later BKN->IND transaction without touching later waivers.
    """
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("source_system") != OFFICIAL or e.get("event_type") != "trade":
            continue
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        day = parse_day(e.get("exact_date"))
        if season in SEASON_BOUNDS and pid and day:
            e["_v11_day"] = day
            by_key[(season, pid)].append(e)

    remove_ids: set[int] = set()
    audit: list[dict[str, Any]] = []
    for (season, pid), rows in by_key.items():
        rows.sort(key=lambda x: (x["_v11_day"], str(x.get("source_reference") or "")))
        for i, earlier in enumerate(rows):
            src = int(earlier.get("source_team_id") or 0)
            dst = int(earlier.get("destination_team_id") or 0)
            if not src or not dst:
                continue
            d0 = date.fromisoformat(earlier["_v11_day"])
            for later in rows[i + 1:]:
                d1 = date.fromisoformat(later["_v11_day"])
                if (d1 - d0).days > 14:
                    break
                if int(later.get("source_team_id") or 0) != src:
                    continue
                later_dst = int(later.get("destination_team_id") or 0)
                if not later_dst or later_dst == dst:
                    continue
                # If the player was explicitly reacquired into the source team
                # between the two trades, both can be legitimate.
                reacquired = False
                for other in events:
                    if str(other.get("season") or "") != season or str(other.get("player_id") or "") != pid:
                        continue
                    od = parse_day(other.get("exact_date"))
                    if not od or not (earlier["_v11_day"] < od < later["_v11_day"]):
                        continue
                    if int(other.get("destination_team_id") or 0) == src and other.get("event_type") in {"trade", "acquire", "claim"}:
                        reacquired = True
                        break
                if reacquired:
                    continue
                remove_ids.add(id(earlier))
                audit.append({
                    "season": season, "player_id": pid,
                    "removed_date": earlier["_v11_day"], "source_team_id": src,
                    "removed_destination_team_id": dst,
                    "superseding_date": later["_v11_day"],
                    "superseding_destination_team_id": later_dst,
                    "removed_raw_text": earlier.get("raw_text"),
                    "superseding_raw_text": later.get("raw_text"),
                })
                break

    cleaned = []
    for e in events:
        e.pop("_v11_day", None)
        if id(e) not in remove_ids:
            cleaned.append(e)
    return cleaned, audit


def is_single_historical_10day_signing(e: dict[str, Any]) -> bool:
    if e.get("source_system") == OFFICIAL:
        return False
    if e.get("event_type") not in {"acquire", "claim"} or not e.get("destination_team_id"):
        return False
    raw = str(e.get("raw_text") or "").casefold()
    if "10-day contract" not in raw:
        return False
    merged_or_renewal_phrases = (
        "two 10-day contracts", "2nd 10-day contract", "second 10-day contract",
        "first of two 10-day contracts", "then signed", "rest of the season",
    )
    return not any(p in raw for p in merged_or_renewal_phrases)


def add_historical_single_10day_departures(events: list[dict[str, Any]], games: dict[tuple[str, int], list[str]]) -> tuple[int, list[dict[str, Any]]]:
    # Make the operation idempotent across fast reruns.
    events[:] = [e for e in events if e.get("derived_boundary_type") != HIST_10DAY]
    by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        season = str(e.get("season") or "")
        pid = str(e.get("player_id") or "")
        if season not in SEASON_BOUNDS or not pid:
            continue
        for team0 in {e.get("source_team_id"), e.get("destination_team_id")} - {None}:
            by_key[(season, pid, int(team0))].append(e)

    added_rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for signing in [e for e in events if is_single_historical_10day_signing(e)]:
        season = str(signing.get("season") or "")
        pid = str(signing.get("player_id") or "")
        team = int(signing.get("destination_team_id") or 0)
        day = parse_day(signing.get("exact_date"))
        if season not in SEASON_BOUNDS or not pid or not team or not day:
            continue
        ss, se = SEASON_BOUNDS[season]
        if not (ss <= day <= se):
            continue
        expiry = deterministic_10day_end(day, season, team, games)
        peers = by_key.get((season, pid, team), [])

        # Source-stated or other explicit departure takes precedence.
        explicit = []
        for p in peers:
            if p is signing or p.get("event_type") not in {"trade", "depart"} or int(p.get("source_team_id") or 0) != team:
                continue
            pd = parse_day(p.get("exact_date"))
            if pd and day < pd <= expiry and p.get("derived_boundary_type") != HIST_10DAY:
                explicit.append(p)
        if explicit:
            continue

        # A later signing/conversion before the natural expiry supersedes this
        # endpoint; do not create a false break in continuous service.
        converted = False
        for p in peers:
            if p is signing or p.get("event_type") not in {"trade", "acquire", "claim"} or int(p.get("destination_team_id") or 0) != team:
                continue
            pd = parse_day(p.get("exact_date"))
            if pd and day < pd < expiry:
                converted = True
                break
        if converted:
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
            "source_system": signing.get("source_system") or "historical transaction source",
            "source_reference": f"{signing.get('source_reference') or 'historical'}#derived-single-10day-v11",
            "identity_resolution": signing.get("identity_resolution") or "inherited_from_signing",
            "team_resolution": "deterministic_historical_single_10day_expiry_v11",
            "raw_text": f"Derived natural endpoint from single 10-day signing on {day}; tenth calendar day inclusive or third team game after signing, whichever is later.",
            "confidence": "high",
            "derived_boundary_type": HIST_10DAY,
            "derived_from_signing_date": day,
            "derived_from_source_system": signing.get("source_system"),
        }
        added_rows.append(row)
        by_key[(season, pid, team)].append(row)
        audit.append({"season": season, "player_id": pid, "team_id": team, "signing": day, "expiry": expiry, "source": signing.get("source_system")})

    events.extend(added_rows)
    return len(added_rows), audit


def self_test() -> None:
    assert parse_day("January 10, 2011") == "2011-01-10"
    assert parse_day("2011-01-10T00:00:00") == "2011-01-10"
    games = {("2010-11", 10): ["2011-01-11", "2011-01-13", "2011-01-21"]}
    assert deterministic_10day_end("2011-01-10", "2010-11", 10, games) == "2011-01-21"
    assert is_single_historical_10day_signing({"source_system": "Basketball-Reference", "event_type": "acquire", "destination_team_id": 10, "raw_text": "Signed X to a 10-day contract."})
    assert not is_single_historical_10day_signing({"source_system": "Basketball-Reference", "event_type": "acquire", "destination_team_id": 10, "raw_text": "Signed X to two 10-day contracts, then signed to a contract for the rest of the season."})
    print("REMAINING OVERLAP SOURCE REPAIR V11 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not EVENTS.exists() or not GAMES.exists():
        raise RuntimeError("normalized transactions and regular-season schedules are required")

    events = read_jsonl_gz(EVENTS)
    before = len(events)
    games = team_game_index(read_jsonl_gz(GAMES))

    official_10day_changed, official_10day_audit = correct_existing_official_10day_dates(events, games)
    events, superseded_trade_audit = remove_conflicting_official_preliminary_trades(events)
    historical_10day_added, historical_10day_audit = add_historical_single_10day_departures(events, games)
    write_jsonl_gz(EVENTS, events)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_events": before,
        "output_events": len(events),
        "existing_official_10day_endpoints_corrected": official_10day_changed,
        "conflicting_preliminary_official_trades_removed": len(superseded_trade_audit),
        "historical_single_10day_departures_added": historical_10day_added,
        "official_10day_audit_sample": official_10day_audit[:100],
        "superseded_trade_audit": superseded_trade_audit,
        "historical_10day_audit_sample": historical_10day_audit[:100],
        "policy": (
            "No QA gate is weakened. Existing official 10-day rows keep their established one-to-one signing linkage but use the tenth calendar day inclusive. "
            "Earlier official trades are suppressed only when a later official trade within 14 days still names the same source team but a different destination. "
            "Historical natural expiries are derived only for unambiguous single 10-day signings; merged two-contract/rest-of-season descriptions are excluded."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("sample") and not k.endswith("audit")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
