from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
CHECKPOINTS = IMPACT / "core_checkpoints"
ROSTER = IMPACT / "roster_tenure"
NORMALIZED = ROSTER / "normalized_transactions.jsonl.gz"
WINDOWS = ROSTER / "player_team_season_windows.jsonl.gz"
AUDIT = ROSTER / "tenure_window_audit.json"
SUMMARY = ROSTER / "tenure_window_summary.json"

SEASON_BOUNDS = {
    "2000-01": ("2000-10-31", "2001-04-18"),
    "2001-02": ("2001-10-30", "2002-04-17"),
    "2002-03": ("2002-10-29", "2003-04-16"),
    "2003-04": ("2003-10-28", "2004-04-14"),
    "2004-05": ("2004-11-02", "2005-04-20"),
    "2005-06": ("2005-11-01", "2006-04-19"),
    "2006-07": ("2006-10-31", "2007-04-18"),
    "2007-08": ("2007-10-30", "2008-04-16"),
    "2008-09": ("2008-10-28", "2009-04-15"),
    "2009-10": ("2009-10-27", "2010-04-14"),
    "2010-11": ("2010-10-26", "2011-04-13"),
    "2011-12": ("2011-12-25", "2012-04-26"),
    "2012-13": ("2012-10-30", "2013-04-17"),
    "2013-14": ("2013-10-29", "2014-04-16"),
    "2014-15": ("2014-10-28", "2015-04-15"),
    "2015-16": ("2015-10-27", "2016-04-13"),
    "2016-17": ("2016-10-25", "2017-04-12"),
    "2017-18": ("2017-10-17", "2018-04-11"),
    "2018-19": ("2018-10-16", "2019-04-10"),
    "2019-20": ("2019-10-22", "2020-08-14"),
    "2020-21": ("2020-12-22", "2021-05-16"),
    "2021-22": ("2021-10-19", "2022-04-10"),
    "2022-23": ("2022-10-18", "2023-04-09"),
    "2023-24": ("2023-10-24", "2024-04-14"),
    "2024-25": ("2024-10-22", "2025-04-13"),
    "2025-26": ("2025-10-21", "2026-04-12"),
}

TEAM_ABBR = {
    1610612737: "ATL", 1610612738: "BOS", 1610612739: "CLE", 1610612740: "NOP",
    1610612741: "CHI", 1610612742: "DAL", 1610612743: "DEN", 1610612744: "GSW",
    1610612745: "HOU", 1610612746: "LAC", 1610612747: "LAL", 1610612748: "MIA",
    1610612749: "MIL", 1610612750: "MIN", 1610612751: "BKN", 1610612752: "NYK",
    1610612753: "ORL", 1610612754: "IND", 1610612755: "PHI", 1610612756: "PHX",
    1610612757: "POR", 1610612758: "SAC", 1610612759: "SAS", 1610612760: "OKC",
    1610612761: "TOR", 1610612762: "UTA", 1610612763: "MEM", 1610612764: "WAS",
    1610612765: "DET", 1610612766: "CHA",
}


def iso_date(value: str) -> str:
    value = (value or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Unsupported transaction date: {value!r}")


def in_regular_season(season: str, day: str) -> bool:
    start, end = SEASON_BOUNDS[season]
    return start <= day <= end


def clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if text.isdigit() and text != "0" else ""


@dataclass
class CoreAffiliation:
    season: str
    player_id: str
    player_name: str
    team_id: int


@dataclass
class Window:
    season: str
    player_id: str
    player_name: str
    team_id: int
    team_abbr: str
    tenure_start: str
    tenure_end: str
    start_reason: str
    end_reason: str
    start_source: str
    end_source: str
    start_source_reference: str | None
    end_source_reference: str | None
    confidence: str
    same_day_resolution: str
    team_games_in_window: int | None
    audit_flags: list[str]


def load_core_affiliations() -> list[CoreAffiliation]:
    if not CHECKPOINTS.exists():
        raise RuntimeError(f"Core checkpoints missing: {CHECKPOINTS}")
    seen: dict[tuple[str, str, int], CoreAffiliation] = {}
    for season_dir in sorted(p for p in CHECKPOINTS.iterdir() if p.is_dir() and p.name in SEASON_BOUNDS):
        season = season_dir.name
        for path in sorted(season_dir.glob("*.json.gz")):
            try:
                team_id = int(path.name.split(".", 1)[0])
            except ValueError:
                continue
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
                seen[(season, player_id, team_id)] = CoreAffiliation(season, player_id, player_name, team_id)
    return sorted(seen.values(), key=lambda a: (a.season, int(a.player_id), a.team_id))


def load_events() -> list[dict[str, Any]]:
    if not NORMALIZED.exists():
        raise RuntimeError(f"Normalized transaction file missing: {NORMALIZED}")
    out = []
    with gzip.open(NORMALIZED, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("player_id") or row.get("season") not in SEASON_BOUNDS:
                continue
            try:
                row["iso_date"] = iso_date(str(row.get("exact_date") or ""))
            except ValueError:
                row["iso_date"] = None
            out.append(row)
    return out


def event_source(event: dict[str, Any]) -> str:
    return str(event.get("source_system") or "unknown")


def source_ref(event: dict[str, Any]) -> str | None:
    value = str(event.get("source_reference") or "").strip()
    return value or None


def choose_latest(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated = [e for e in events if e.get("iso_date")]
    return max(dated, key=lambda e: (e["iso_date"], str(e.get("source_reference") or ""))) if dated else None


def choose_earliest(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated = [e for e in events if e.get("iso_date")]
    return min(dated, key=lambda e: (e["iso_date"], str(e.get("source_reference") or ""))) if dated else None


def build_one(
    affiliation: CoreAffiliation,
    player_teams: set[int],
    events: list[dict[str, Any]],
) -> Window:
    season_start, season_end = SEASON_BOUNDS[affiliation.season]
    team_id = affiliation.team_id
    relevant = [
        e for e in events
        if e.get("season") == affiliation.season
        and str(e.get("player_id")) == affiliation.player_id
        and (e.get("source_team_id") == team_id or e.get("destination_team_id") == team_id)
        and e.get("iso_date")
    ]
    acquisitions = [e for e in relevant if e.get("destination_team_id") == team_id and e.get("event_type") in {"trade", "acquire", "claim"}]
    departures = [e for e in relevant if e.get("source_team_id") == team_id and e.get("event_type") in {"trade", "depart"}]

    start_event = choose_earliest([e for e in acquisitions if in_regular_season(affiliation.season, e["iso_date"])])
    end_event = choose_latest([e for e in departures if in_regular_season(affiliation.season, e["iso_date"])])

    flags: list[str] = []
    confidence = "high"

    if start_event:
        start = start_event["iso_date"]
        start_reason = f"{start_event.get('event_type')}_into_team"
        start_source = event_source(start_event)
        start_reference = source_ref(start_event)
    else:
        start = season_start
        start_reason = "season_open_roster_continuity"
        start_source = "core affiliation + no in-season acquisition event"
        start_reference = None
        if len(player_teams) > 1:
            flags.append("multi_team_affiliation_without_acquisition_boundary")
            confidence = "review"

    if end_event:
        end = end_event["iso_date"]
        end_reason = f"{end_event.get('event_type')}_out_of_team"
        end_source = event_source(end_event)
        end_reference = source_ref(end_event)
    else:
        end = season_end
        end_reason = "season_close_roster_continuity"
        end_source = "core affiliation + no in-season departure event"
        end_reference = None
        if len(player_teams) > 1:
            flags.append("multi_team_affiliation_without_departure_boundary")
            confidence = "review"

    if start > end:
        flags.append("invalid_boundary_order")
        confidence = "review"

    same_day = "not_applicable"
    if start_event or end_event:
        same_day = "transaction_date_boundary; game-day inclusion requires schedule/time audit"
        flags.append("same_day_game_check_required")
        if confidence == "high":
            confidence = "provisional_high"

    return Window(
        season=affiliation.season,
        player_id=affiliation.player_id,
        player_name=affiliation.player_name,
        team_id=team_id,
        team_abbr=TEAM_ABBR.get(team_id, str(team_id)),
        tenure_start=start,
        tenure_end=end,
        start_reason=start_reason,
        end_reason=end_reason,
        start_source=start_source,
        end_source=end_source,
        start_source_reference=start_reference,
        end_source_reference=end_reference,
        confidence=confidence,
        same_day_resolution=same_day,
        team_games_in_window=None,
        audit_flags=flags,
    )


def build_windows(affiliations: list[CoreAffiliation], events: list[dict[str, Any]]) -> list[Window]:
    teams_by_player_season: dict[tuple[str, str], set[int]] = defaultdict(set)
    for a in affiliations:
        teams_by_player_season[(a.season, a.player_id)].add(a.team_id)
    return [
        build_one(a, teams_by_player_season[(a.season, a.player_id)], events)
        for a in affiliations
    ]


def write_outputs(windows: list[Window]) -> dict[str, Any]:
    ROSTER.mkdir(parents=True, exist_ok=True)
    with gzip.open(WINDOWS, "wt", encoding="utf-8") as handle:
        for window in windows:
            handle.write(json.dumps(asdict(window), ensure_ascii=False) + "\n")

    review = [asdict(w) for w in windows if w.confidence == "review"]
    same_day = [asdict(w) for w in windows if "same_day_game_check_required" in w.audit_flags]
    multi_team = [asdict(w) for w in windows if any(f.startswith("multi_team_affiliation") for f in w.audit_flags)]
    invalid = [asdict(w) for w in windows if "invalid_boundary_order" in w.audit_flags]
    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "review_count": len(review),
        "same_day_game_check_count": len(same_day),
        "multi_team_boundary_gap_count": len(multi_team),
        "invalid_boundary_order_count": len(invalid),
        "review_windows": review,
        "same_day_game_checks": same_day,
        "multi_team_boundary_gaps": multi_team,
        "invalid_boundary_windows": invalid,
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_count": len(windows),
        "seasons": sorted({w.season for w in windows}),
        "players": len({w.player_id for w in windows}),
        "high_confidence": sum(w.confidence == "high" for w in windows),
        "provisional_high": sum(w.confidence == "provisional_high" for w in windows),
        "review": len(review),
        "same_day_game_checks": len(same_day),
        "multi_team_boundary_gaps": len(multi_team),
        "invalid_boundary_order": len(invalid),
        "team_game_counts_populated": sum(w.team_games_in_window is not None for w in windows),
        "output": str(WINDOWS),
        "audit": str(AUDIT),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    affiliations = [
        CoreAffiliation("2023-24", "203500", "Steven Adams", 1610612763),
        CoreAffiliation("2023-24", "203500", "Steven Adams", 1610612745),
        CoreAffiliation("2003-04", "123", "Example Trade", 1610612737),
        CoreAffiliation("2003-04", "123", "Example Trade", 1610612765),
        CoreAffiliation("2003-04", "456", "Season Long", 1610612738),
    ]
    events = [
        {"season": "2023-24", "player_id": "203500", "event_type": "trade", "source_team_id": 1610612763, "destination_team_id": 1610612745, "iso_date": "2024-02-01", "source_system": "Official NBA Player Movement feed", "source_reference": "fixture#adams"},
        {"season": "2003-04", "player_id": "123", "event_type": "trade", "source_team_id": 1610612737, "destination_team_id": 1610612765, "iso_date": "2004-02-19", "source_system": "Basketball-Reference via Internet Archive", "source_reference": "fixture#classic"},
    ]
    windows = build_windows(affiliations, events)
    by_key = {(w.season, w.player_id, w.team_id): w for w in windows}
    mem = by_key[("2023-24", "203500", 1610612763)]
    hou = by_key[("2023-24", "203500", 1610612745)]
    assert mem.tenure_start == "2023-10-24" and mem.tenure_end == "2024-02-01"
    assert hou.tenure_start == "2024-02-01" and hou.tenure_end == "2024-04-14"
    atl = by_key[("2003-04", "123", 1610612737)]
    det = by_key[("2003-04", "123", 1610612765)]
    assert atl.tenure_end == "2004-02-19" and det.tenure_start == "2004-02-19"
    season_long = by_key[("2003-04", "456", 1610612738)]
    assert season_long.tenure_start == "2003-10-28" and season_long.tenure_end == "2004-04-14"
    print("TENURE WINDOW BUILDER SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    affiliations = load_core_affiliations()
    events = load_events()
    windows = build_windows(affiliations, events)
    summary = write_outputs(windows)
    print(json.dumps(summary, indent=2), flush=True)
    if summary["invalid_boundary_order"]:
        raise RuntimeError("Invalid tenure boundary order detected; see audit report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
