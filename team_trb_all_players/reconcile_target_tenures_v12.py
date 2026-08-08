from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from build_roster_tenure_windows import SEASON_BOUNDS

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
WINDOWS = ROOT / "player_team_season_windows.jsonl.gz"
TARGETS = ROOT / "remaining_overlap_event_chains_v11.json"
SUMMARY = ROOT / "target_tenure_reconciliation_v12_summary.json"


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (
        str(r.get("season") or ""), int(str(r.get("player_id") or 0)),
        int(r.get("team_id") or 0), str(r.get("tenure_start") or ""), str(r.get("tenure_end") or ""),
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


def event_role(e: dict[str, Any], team: int) -> str | None:
    into = int(e.get("destination_team_id") or 0) == team and e.get("event_type") in {"trade", "acquire", "claim"}
    out = int(e.get("source_team_id") or 0) == team and e.get("event_type") in {"trade", "depart"}
    if into and out:
        return "both"
    if into:
        return "in"
    if out:
        return "out"
    return None


def target_cases() -> dict[tuple[str, str], dict[str, Any]]:
    data = json.loads(TARGETS.read_text(encoding="utf-8"))
    return {
        (str(c["season"]), str(c["player_id"])): c
        for c in data.get("cases") or []
    }


def event_index(events: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for original in events:
        season, pid = str(original.get("season") or ""), str(original.get("player_id") or "")
        day = parse_day(original.get("exact_date"))
        if season not in SEASON_BOUNDS or not pid or not day:
            continue
        e = dict(original); e["_v12_day"] = day
        out[(season, pid)].append(e)
    for key in out:
        out[key].sort(key=lambda e: (e["_v12_day"], str(e.get("source_reference") or ""), str(e.get("event_type") or "")))
    return dict(out)


def compact_source(e: dict[str, Any] | None, fallback: str) -> str:
    return str(e.get("source_system") or fallback) if e else fallback


def source_ref(e: dict[str, Any] | None) -> str | None:
    value = str(e.get("source_reference") or "").strip() if e else ""
    return value or None


def team_events(all_events: list[dict[str, Any]], team: int) -> list[dict[str, Any]]:
    return [e for e in all_events if event_role(e, team)]


def initial_state(team: int, events: list[dict[str, Any]], season_start: str, is_core: bool) -> tuple[bool | None, dict[str, Any] | None, str]:
    before = [e for e in events if e["_v12_day"] < season_start]
    if before:
        latest_day = before[-1]["_v12_day"]
        same_day = [e for e in before if e["_v12_day"] == latest_day]
        roles = {event_role(e, team) for e in same_day}
        if "in" in roles and "out" not in roles:
            return True, next(e for e in reversed(same_day) if event_role(e, team) == "in"), "latest_preseason_acquisition"
        if "out" in roles and "in" not in roles:
            return False, next(e for e in reversed(same_day) if event_role(e, team) == "out"), "latest_preseason_departure"
        if "both" in roles or {"in", "out"}.issubset(roles):
            return (True if is_core else None), same_day[-1], "same_day_preseason_transition_core_resolves_active" if is_core else "same_day_preseason_transition_ambiguous"

    in_season = [e for e in events if e["_v12_day"] >= season_start]
    if in_season:
        day = in_season[0]["_v12_day"]
        first = [e for e in in_season if e["_v12_day"] == day]
        roles = {event_role(e, team) for e in first}
        if "out" in roles and "in" not in roles:
            return True, next(e for e in first if event_role(e, team) == "out"), "first_regular_event_is_departure"
        if "in" in roles and "out" not in roles:
            return False, next(e for e in first if event_role(e, team) == "in"), "first_regular_event_is_acquisition"
        if "both" in roles or {"in", "out"}.issubset(roles):
            return (True if is_core else False), first[-1], "first_regular_same_day_transition"
    return (None if is_core else False), None, "no_transaction_state_evidence"


def rebuild_team(
    season: str, pid: str, player_name: str, team: int, events: list[dict[str, Any]],
    original_rows: list[dict[str, Any]], is_core: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ss, se = SEASON_BOUNDS[season]
    exemplar = next((w for w in original_rows if int(w.get("team_id") or 0) == team), None)
    active, initial_evidence, initial_reason = initial_state(team, events, ss, is_core)
    if active is None:
        return [], {"team_id": team, "supported": False, "reason": initial_reason, "segments": 0}

    by_day: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"in": [], "out": [], "both": []})
    for e in events:
        day = e["_v12_day"]
        if not (ss <= day <= se):
            continue
        role = event_role(e, team)
        if role:
            by_day[day][role].append(e)

    current_start = ss if active else None
    current_start_event: dict[str, Any] | None = None
    segments: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, str]] = []
    same_day_continuity = 0
    pass_through = 0

    for day in sorted(by_day):
        ins = by_day[day]["in"] + by_day[day]["both"]
        outs = by_day[day]["out"] + by_day[day]["both"]
        if ins and outs:
            if active:
                # Same-team renewal/conversion. Remaining rostered is the conservative exact interpretation.
                same_day_continuity += 1
                continue
            segments.append((day, day, ins[0], outs[0], "same_day_pass_through"))
            pass_through += 1
            continue
        if outs:
            if active:
                segments.append((current_start or ss, day, current_start_event, outs[0], "event_closed"))
                active = False; current_start = None; current_start_event = None
            continue
        if ins:
            if not active:
                active = True; current_start = day; current_start_event = ins[0]
            # Re-sign/conversion while already active preserves continuity.
            continue
    if active:
        segments.append((current_start or ss, se, current_start_event, None, "season_close"))

    # Exact dedupe.
    cleaned = []
    seen = set()
    for seg in segments:
        a, b = seg[0], seg[1]
        if a > b or (a, b) in seen:
            continue
        seen.add((a, b)); cleaned.append(seg)

    output = []
    abbr = str((exemplar or {}).get("team_abbr") or team)
    base_flags = [f for f in list((exemplar or {}).get("audit_flags") or []) if f not in {
        "invalid_boundary_order", "multi_team_affiliation_without_acquisition_boundary",
        "multi_team_affiliation_without_departure_boundary", "same_day_reacquisition_collision",
    }]
    for idx, (a, b, start_e, end_e, seg_reason) in enumerate(cleaned, start=1):
        row = dict(exemplar or {})
        row.update({
            "season": season, "player_id": pid, "player_name": player_name,
            "team_id": team, "team_abbr": abbr,
            "tenure_start": a, "tenure_end": b,
            "team_games_in_window": None,
            "confidence": "provisional_high",
            "start_reason": f"{start_e.get('event_type')}_into_team" if start_e else "season_open_roster_continuity_v12",
            "end_reason": f"{end_e.get('event_type')}_out_of_team" if end_e else "season_close_roster_continuity_v12",
            "start_source": compact_source(start_e, "v12 exact transaction chronology" if initial_evidence else "core affiliation + v12 transaction-state validation"),
            "end_source": compact_source(end_e, "v12 exact transaction chronology"),
            "start_source_reference": source_ref(start_e) or source_ref(initial_evidence),
            "end_source_reference": source_ref(end_e),
            "same_day_resolution": "deterministic_transaction_day_policy",
            "segment_index": idx, "segment_count": len(cleaned),
        })
        flags = base_flags + ["v12_event_state_reconstructed"]
        if seg_reason == "same_day_pass_through": flags.append("same_day_pass_through_zero_effective_service")
        if start_e and start_e.get("derived_boundary_type"): flags.append(str(start_e["derived_boundary_type"]))
        if end_e and end_e.get("derived_boundary_type"): flags.append(str(end_e["derived_boundary_type"]))
        row["audit_flags"] = sorted(set(flags))
        output.append(row)
    return output, {
        "team_id": team, "supported": True, "initial_state_reason": initial_reason,
        "segments": len(output), "same_day_continuity": same_day_continuity, "pass_through": pass_through,
    }


def intervals_cover(start: str, end: str, intervals: list[tuple[str, str]]) -> bool:
    if not intervals:
        return False
    ints = sorted((date.fromisoformat(a).toordinal(), date.fromisoformat(b).toordinal()) for a, b in intervals if a <= b)
    target_start, target_end = date.fromisoformat(start).toordinal(), date.fromisoformat(end).toordinal()
    cursor = target_start
    for a, b in ints:
        if b < cursor:
            continue
        if a > cursor:
            return False
        cursor = max(cursor, b + 1)
        if cursor > target_end:
            return True
    return cursor > target_end


def self_test() -> None:
    # Preseason waiver means no opening roster service.
    ev = [{"_v12_day": "2003-10-21", "event_type": "depart", "source_team_id": 10, "destination_team_id": None}]
    state, _, reason = initial_state(10, ev, "2003-10-28", True)
    assert state is False and reason == "latest_preseason_departure"
    # In-season trade source is active from opener, destination starts at transaction boundary.
    source = [{"_v12_day": "2026-02-01", "event_type": "trade", "source_team_id": 10, "destination_team_id": 20, "source_system": "test"}]
    s, _, _ = initial_state(10, source, "2025-10-21", True); d, _, _ = initial_state(20, source, "2025-10-21", False)
    assert s is True and d is False
    assert intervals_cover("2000-10-31", "2001-04-18", [("2000-10-31", "2001-01-12"), ("2001-01-12", "2001-04-18")])
    print("TARGET TENURE RECONCILIATION V12 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if not EVENTS.exists() or not WINDOWS.exists() or not TARGETS.exists():
        raise RuntimeError("normalized events, raw windows, and v11 target diagnostic are required")

    windows = read_jsonl_gz(WINDOWS); events = read_jsonl_gz(EVENTS); targets = target_cases(); eindex = event_index(events)
    original_by_case: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    untouched = []
    for w in windows:
        key = (str(w.get("season") or ""), str(w.get("player_id") or ""))
        if key in targets: original_by_case[key].append(w)
        else: untouched.append(w)

    rebuilt_all = []; audits = []; phantom_dropped = []; unresolved_fallback = []
    for key, case in sorted(targets.items()):
        season, pid = key; ss, se = SEASON_BOUNDS[season]
        original = original_by_case.get(key, [])
        player_name = str(case.get("player_name") or (original[0].get("player_name") if original else pid))
        core_teams = {int(w.get("team_id") or 0) for w in original if w.get("team_id") and "official_transaction_derived_affiliation" not in set(w.get("audit_flags") or [])}
        all_events = eindex.get(key, [])
        event_teams = {int(t) for e in all_events for t in (e.get("source_team_id"), e.get("destination_team_id")) if t}
        teams = sorted((core_teams | event_teams) - {0})

        supported_rows = []; unsupported_teams = []; case_audit = {"season": season, "player_id": pid, "player_name": player_name, "teams": []}
        for team in teams:
            tev = team_events(all_events, team)
            rows, audit = rebuild_team(season, pid, player_name, team, tev, original, team in core_teams)
            case_audit["teams"].append(audit)
            if audit.get("supported"):
                supported_rows.extend(rows)
            elif team in core_teams:
                unsupported_teams.append(team)

        supported_intervals = [(r["tenure_start"], r["tenure_end"]) for r in supported_rows]
        full_supported_coverage = intervals_cover(ss, se, supported_intervals)
        fallback_rows = []
        for team in unsupported_teams:
            originals = [dict(w) for w in original if int(w.get("team_id") or 0) == team]
            if full_supported_coverage:
                phantom_dropped.append({"season": season, "player_id": pid, "player_name": player_name, "team_id": team, "reason": "other exact transaction-derived affiliations cover entire regular season"})
                continue
            for row in originals:
                row["confidence"] = "review"
                flags = list(row.get("audit_flags") or []) + ["v12_no_transaction_support_fallback"]
                row["audit_flags"] = sorted(set(flags))
                fallback_rows.append(row)
            unresolved_fallback.append({"season": season, "player_id": pid, "player_name": player_name, "team_id": team})

        # Event-derived teams replace target-case builder windows. Unsupported core-only teams are retained only when exact chronology does not cover the season.
        rebuilt_case = supported_rows + fallback_rows
        rebuilt_all.extend(rebuilt_case)
        case_audit.update({
            "input_windows": len(original), "output_windows": len(rebuilt_case),
            "event_count": len(all_events), "unsupported_core_teams": unsupported_teams,
            "full_season_covered_by_supported_intervals": full_supported_coverage,
        })
        audits.append(case_audit)

    output = untouched + rebuilt_all
    write_jsonl_gz(WINDOWS, output)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "target_player_seasons": len(targets),
        "input_windows": len(windows), "output_windows": len(output),
        "target_windows_rebuilt": len(rebuilt_all),
        "unsupported_core_phantom_windows_dropped": len(phantom_dropped),
        "unsupported_core_teams_still_requiring_review": len(unresolved_fallback),
        "phantom_drop_audit": phantom_dropped,
        "unresolved_fallback_audit": unresolved_fallback,
        "case_audit": audits,
        "policy": (
            "Only v11 overlap player-seasons are rewritten. Exact transaction chronology determines season-open state, in-season acquisitions/departures and same-team renewals. "
            "A preseason departure removes season-open service unless a later acquisition restores it. A core-only team with zero transaction support is dropped as a phantom only when other transaction-supported affiliations cover the entire regular season; otherwise it remains review-gated."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_audit")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
