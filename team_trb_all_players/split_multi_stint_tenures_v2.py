from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import split_multi_stint_tenures as old

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
WINDOWS = ROOT / "player_team_season_windows.jsonl.gz"
SUMMARY = ROOT / "multi_stint_summary.json"


def read_rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda w: (
        str(w.get("season") or ""), int(str(w.get("player_id") or 0)), int(w.get("team_id") or 0),
        str(w.get("tenure_start") or ""), str(w.get("tenure_end") or ""),
    ))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def event_role(event: dict[str, Any], team: int) -> str | None:
    return old.event_role(event, team)


def prepare_events() -> tuple[dict[tuple[str, str, int], list[dict[str, Any]]], int]:
    by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    normalized_dates = 0
    bad_dates: list[dict[str, Any]] = []
    for original in read_rows(EVENTS):
        season = str(original.get("season") or "")
        pid = str(original.get("player_id") or "")
        raw_day = str(original.get("exact_date") or "").strip()
        if season not in old.SEASON_BOUNDS or not pid or not raw_day:
            continue
        day = old.iso_date(raw_day)
        if day is None:
            bad_dates.append({"season": season, "player_id": pid, "exact_date": raw_day})
            continue
        if day != raw_day:
            normalized_dates += 1
        ss, se = old.SEASON_BOUNDS[season]
        if not (ss <= day <= se):
            continue
        event = dict(original)
        event["_iso_date"] = day
        for team0 in {event.get("source_team_id"), event.get("destination_team_id")} - {None}:
            team = int(team0)
            if event_role(event, team):
                by_key[(season, pid, team)].append(event)
    if bad_dates:
        raise RuntimeError(f"Unparseable normalized transaction dates: {bad_dates[:20]}")
    for key in by_key:
        by_key[key].sort(key=lambda e: (e["_iso_date"], str(e.get("source_reference") or "")))
    return dict(by_key), normalized_dates


def choose_event(events: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    candidates = [e for e in events if event_role(e, int(e.get("source_team_id") or e.get("destination_team_id") or 0))]
    for e in events:
        team_ids = {e.get("source_team_id"), e.get("destination_team_id")} - {None}
        for team in team_ids:
            if event_role(e, int(team)) == role:
                return e
    return candidates[0] if candidates else None


def reason_for(event: dict[str, Any] | None, suffix: str, fallback: str) -> str:
    if not event:
        return fallback
    return f"{event.get('event_type')}_{suffix}"


def ref_for(event: dict[str, Any] | None) -> str | None:
    if not event:
        return None
    value = str(event.get("source_reference") or "").strip()
    return value or None


def needs_rebuild(window: dict[str, Any], events: list[dict[str, Any]], team: int) -> bool:
    if len(events) < 2:
        return False
    by_day: dict[str, set[str]] = defaultdict(set)
    sequence: list[tuple[str, str]] = []
    derived = False
    for e in events:
        role = event_role(e, team)
        if role in {"in", "out"}:
            by_day[e["_iso_date"]].add(role)
            sequence.append((e["_iso_date"], role))
        if e.get("derived_boundary_type") == "10_day_contract_natural_expiry":
            derived = True
    same_day_both = any({"in", "out"}.issubset(roles) for roles in by_day.values())
    out_then_later_in = any(
        role == "out" and any(r2 == "in" and d2 >= day for d2, r2 in sequence[i + 1:])
        for i, (day, role) in enumerate(sequence)
    )
    return derived or same_day_both or out_then_later_in


def rebuild(window: dict[str, Any], events: list[dict[str, Any]], team: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    season = str(window["season"])
    ss, se = old.SEASON_BOUNDS[season]
    by_day: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"in": [], "out": []})
    for e in events:
        role = event_role(e, team)
        if role in {"in", "out"}:
            by_day[e["_iso_date"]][role].append(e)

    days = sorted(by_day)
    if not days:
        return [window], {"same_day_pass_through": 0, "same_day_continuity": 0, "segments": 1}

    first_roles = {r for r in ("in", "out") if by_day[days[0]][r]}
    flags = set(window.get("audit_flags") or [])
    if first_roles == {"out"}:
        active = True
    elif first_roles == {"in"}:
        active = False
    elif first_roles == {"in", "out"}:
        active = (
            str(window.get("tenure_start") or "") == ss
            and "official_transaction_derived_affiliation" not in flags
            and "zero_core_minutes_candidate" not in flags
        )
    else:
        active = str(window.get("tenure_start") or "") == ss

    current_start = ss if active else None
    current_start_event: dict[str, Any] | None = None
    segments: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, bool]] = []
    same_day_pass_through = 0
    same_day_continuity = 0

    for day in days:
        ins = by_day[day]["in"]
        outs = by_day[day]["out"]
        if ins and outs:
            if active:
                # Same-team renewal / contract transition while already rostered:
                # preserve continuous service rather than inventing a gap.
                same_day_continuity += 1
                continue
            # Same-day in-and-out while previously inactive is a pass-through
            # affiliation. Preserve the raw transaction day as a zero-length
            # window; the downstream policy shifts incoming service to next day,
            # so this contributes zero effective team games and cannot overlap.
            segments.append((day, day, ins[0], outs[0], True))
            same_day_pass_through += 1
            continue

        if outs:
            if active:
                segments.append((current_start or ss, day, current_start_event, outs[0], False))
                active = False
                current_start = None
                current_start_event = None
            continue

        if ins and not active:
            active = True
            current_start = day
            current_start_event = ins[0]

    if active:
        segments.append((current_start or ss, se, current_start_event, None, False))

    cleaned: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, bool]] = []
    seen = set()
    for seg in segments:
        a, b, _, _, _ = seg
        if a > b:
            continue
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(seg)

    if not cleaned:
        return [window], {"same_day_pass_through": same_day_pass_through, "same_day_continuity": same_day_continuity, "segments": 1}

    out_rows: list[dict[str, Any]] = []
    total = len(cleaned)
    all_refs = sorted({str(e.get("source_reference") or "") for e in events if e.get("source_reference")})
    for idx, (a, b, start_event, end_event, pass_through) in enumerate(cleaned, start=1):
        row = dict(window)
        row["tenure_start"] = a
        row["tenure_end"] = b
        row["team_games_in_window"] = None
        row["confidence"] = "provisional_high"
        row["start_reason"] = reason_for(start_event, "into_team", "season_open_roster_continuity")
        row["end_reason"] = reason_for(end_event, "out_of_team", "season_close_roster_continuity")
        row["start_source"] = str(start_event.get("source_system")) if start_event else str(window.get("start_source") or "core affiliation")
        row["end_source"] = str(end_event.get("source_system")) if end_event else str(window.get("end_source") or "core affiliation")
        row["start_source_reference"] = ref_for(start_event)
        row["end_source_reference"] = ref_for(end_event)
        row["same_day_resolution"] = "deterministic_transaction_day_policy"
        new_flags = [
            f for f in list(window.get("audit_flags") or [])
            if f not in {
                "multi_team_affiliation_without_acquisition_boundary",
                "multi_team_affiliation_without_departure_boundary",
                "invalid_boundary_order",
                "same_day_reacquisition_collision",
            }
        ]
        new_flags += ["chronology_rebuilt_from_transaction_events", f"segment_{idx}_of_{total}"]
        if total > 1:
            new_flags.append("multi_stint_segment")
        if pass_through:
            new_flags.append("same_day_pass_through_zero_effective_service")
        if start_event and start_event.get("derived_boundary_type"):
            new_flags.append(str(start_event["derived_boundary_type"]))
        if end_event and end_event.get("derived_boundary_type"):
            new_flags.append(str(end_event["derived_boundary_type"]))
        row["audit_flags"] = sorted(set(new_flags))
        row["segment_index"] = idx
        row["segment_count"] = total
        row["segment_source_references"] = all_refs
        out_rows.append(row)

    return out_rows, {
        "same_day_pass_through": same_day_pass_through,
        "same_day_continuity": same_day_continuity,
        "segments": len(out_rows),
    }


def self_test() -> None:
    # Same-day trade pass-through must not become season-open + season-close.
    w = {
        "season": "2023-24", "player_id": "1", "team_id": 1610612754,
        "tenure_start": "2024-01-17", "tenure_end": "2024-01-17",
        "audit_flags": ["official_transaction_derived_affiliation", "zero_core_minutes_candidate"],
        "confidence": "provisional_high",
    }
    ev = [
        {"_iso_date": "2024-01-17", "event_type": "trade", "destination_team_id": 1610612754, "source_team_id": 1610612740, "source_reference": "a", "source_system": "Official NBA Player Movement feed"},
        {"_iso_date": "2024-01-17", "event_type": "trade", "destination_team_id": 1610612761, "source_team_id": 1610612754, "source_reference": "b", "source_system": "Official NBA Player Movement feed"},
    ]
    rows, stats = rebuild(w, ev, 1610612754)
    assert len(rows) == 1 and rows[0]["tenure_start"] == rows[0]["tenure_end"] == "2024-01-17"
    assert stats["same_day_pass_through"] == 1

    # Consecutive 10-day contracts on the same team remain continuous.
    w2 = {
        "season": "2021-22", "player_id": "2", "team_id": 1610612750,
        "tenure_start": "2021-12-27", "tenure_end": "2022-01-16",
        "audit_flags": [], "confidence": "provisional_high",
    }
    ev2 = [
        {"_iso_date": "2021-12-27", "event_type": "acquire", "destination_team_id": 1610612750, "source_team_id": None, "source_reference": "c", "source_system": "Official NBA Player Movement feed"},
        {"_iso_date": "2022-01-06", "event_type": "depart", "destination_team_id": None, "source_team_id": 1610612750, "source_reference": "d", "source_system": "Official NBA Player Movement feed", "derived_boundary_type": "10_day_contract_natural_expiry"},
        {"_iso_date": "2022-01-06", "event_type": "acquire", "destination_team_id": 1610612750, "source_team_id": None, "source_reference": "e", "source_system": "Official NBA Player Movement feed"},
        {"_iso_date": "2022-01-16", "event_type": "depart", "destination_team_id": None, "source_team_id": 1610612750, "source_reference": "f", "source_system": "Official NBA Player Movement feed", "derived_boundary_type": "10_day_contract_natural_expiry"},
    ]
    rows2, stats2 = rebuild(w2, ev2, 1610612750)
    assert len(rows2) == 1 and rows2[0]["tenure_start"] == "2021-12-27" and rows2[0]["tenure_end"] == "2022-01-16"
    assert stats2["same_day_continuity"] == 1
    print("MULTI-STINT V2 SELF-TEST PASSED")


def main() -> int:
    windows = read_rows(WINDOWS)
    events_by_key, normalized_dates = prepare_events()
    output: list[dict[str, Any]] = []
    rebuilt_keys = 0
    pass_throughs = 0
    continuities = 0
    extra_segments = 0

    for window in windows:
        key = (str(window.get("season") or ""), str(window.get("player_id") or ""), int(window.get("team_id") or 0))
        events = events_by_key.get(key, [])
        if not needs_rebuild(window, events, key[2]):
            output.append(window)
            continue
        rebuilt, stats = rebuild(window, events, key[2])
        output.extend(rebuilt)
        rebuilt_keys += 1
        pass_throughs += stats["same_day_pass_through"]
        continuities += stats["same_day_continuity"]
        extra_segments += max(0, stats["segments"] - 1)

    write_rows(WINDOWS, output)
    summary = {
        "input_windows": len(windows),
        "output_windows": len(output),
        "player_team_seasons_rebuilt": rebuilt_keys,
        "extra_segments_created": extra_segments,
        "same_day_pass_through_zero_effective_windows": pass_throughs,
        "same_day_same_team_continuities_preserved": continuities,
        "historical_event_dates_normalized": normalized_dates,
        "policy": (
            "Same-day in+out while previously inactive is a zero-effective-service pass-through; "
            "same-day in+out while already active preserves continuous same-team roster service. "
            "Derived 10-day expiries participate as exact departure boundaries."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
