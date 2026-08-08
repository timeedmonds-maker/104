from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
WINDOWS = ROOT / "player_team_season_windows.jsonl.gz"
SUMMARY = ROOT / "multi_stint_summary.json"

SEASON_BOUNDS = {
    "2000-01": ("2000-10-31", "2001-04-18"), "2001-02": ("2001-10-30", "2002-04-17"),
    "2002-03": ("2002-10-29", "2003-04-16"), "2003-04": ("2003-10-28", "2004-04-14"),
    "2004-05": ("2004-11-02", "2005-04-20"), "2005-06": ("2005-11-01", "2006-04-19"),
    "2006-07": ("2006-10-31", "2007-04-18"), "2007-08": ("2007-10-30", "2008-04-16"),
    "2008-09": ("2008-10-28", "2009-04-15"), "2009-10": ("2009-10-27", "2010-04-14"),
    "2010-11": ("2010-10-26", "2011-04-13"), "2011-12": ("2011-12-25", "2012-04-26"),
    "2012-13": ("2012-10-30", "2013-04-17"), "2013-14": ("2013-10-29", "2014-04-16"),
    "2014-15": ("2014-10-28", "2015-04-15"), "2015-16": ("2015-10-27", "2016-04-13"),
    "2016-17": ("2016-10-25", "2017-04-12"), "2017-18": ("2017-10-17", "2018-04-11"),
    "2018-19": ("2018-10-16", "2019-04-10"), "2019-20": ("2019-10-22", "2020-08-14"),
    "2020-21": ("2020-12-22", "2021-05-16"), "2021-22": ("2021-10-19", "2022-04-10"),
    "2022-23": ("2022-10-18", "2023-04-09"), "2023-24": ("2023-10-24", "2024-04-14"),
    "2024-25": ("2024-10-22", "2025-04-13"), "2025-26": ("2025-10-21", "2026-04-12"),
}


def read_rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def iso_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    # Official NBA movement rows can be date-only ISO strings or ISO datetimes
    # such as 2015-07-01T00:00:00. Normalize both without weakening validation.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass

    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def event_role(e, team):
    into = e.get("destination_team_id") == team and e.get("event_type") in {"trade", "acquire", "claim"}
    out = e.get("source_team_id") == team and e.get("event_type") in {"trade", "depart"}
    if into and out:
        return "both"
    if into:
        return "in"
    if out:
        return "out"
    return None


def main() -> int:
    windows = list(read_rows(WINDOWS))
    events_by_key = defaultdict(list)
    normalized_date_events = 0
    unparseable_event_dates = []

    for original in read_rows(EVENTS):
        season = original.get("season")
        pid = str(original.get("player_id") or "")
        raw_day = str(original.get("exact_date") or "").strip()
        if season not in SEASON_BOUNDS or not pid or not raw_day:
            continue

        day = iso_date(raw_day)
        if day is None:
            unparseable_event_dates.append({
                "season": season,
                "player_id": pid,
                "exact_date": raw_day,
                "source_reference": original.get("source_reference"),
            })
            continue
        if day != raw_day:
            normalized_date_events += 1

        ss, se = SEASON_BOUNDS[season]
        if not (ss <= day <= se):
            continue

        e = dict(original)
        e["_iso_date"] = day
        for team in {e.get("source_team_id"), e.get("destination_team_id")} - {None}:
            role = event_role(e, int(team))
            if role:
                events_by_key[(season, pid, int(team))].append(e)

    if unparseable_event_dates:
        print(json.dumps({
            "unparseable_event_date_count": len(unparseable_event_dates),
            "examples": unparseable_event_dates[:50],
        }, indent=2))
        raise RuntimeError("Unparseable normalized transaction dates in multi-stint splitter")

    output = []
    split_keys = 0
    extra_segments = 0
    ambiguous_same_day = 0

    for w in windows:
        key = (w["season"], str(w["player_id"]), int(w["team_id"]))
        events = events_by_key.get(key, [])
        roles = [(e["_iso_date"], event_role(e, key[2]), e) for e in events]
        roles.sort(key=lambda x: (x[0], 0 if x[1] == "out" else 1, str(x[2].get("source_reference") or "")))

        # Only replace the builder's single interval when the event history
        # demonstrates a departure followed by a later reacquisition.
        role_sequence = [r for _, r, _ in roles if r in {"in", "out"}]
        needs_split = any(
            role_sequence[i] == "out" and "in" in role_sequence[i + 1:]
            for i in range(len(role_sequence))
        )
        if not needs_split:
            output.append(w)
            continue

        ss, se = SEASON_BOUNDS[w["season"]]
        first_role = role_sequence[0] if role_sequence else None
        active = first_role == "out"
        current_start = ss if active else None
        segments = []
        refs = []
        same_day_roles = defaultdict(set)

        for day, role, e in roles:
            same_day_roles[day].add(role)
            ref = e.get("source_reference")
            if ref:
                refs.append(ref)
            if role == "in":
                if not active:
                    current_start = day
                    active = True
            elif role == "out":
                if active:
                    segments.append((current_start or ss, day))
                    active = False
                    current_start = None
        if active:
            segments.append((current_start or ss, se))

        segments = [(a, b) for a, b in segments if a <= b]
        if len(segments) <= 1:
            output.append(w)
            continue

        split_keys += 1
        extra_segments += len(segments) - 1
        day_ambiguous = [d for d, rs in same_day_roles.items() if "in" in rs and "out" in rs]
        ambiguous_same_day += len(day_ambiguous)
        for idx, (a, b) in enumerate(segments, start=1):
            row = dict(w)
            row["tenure_start"] = a
            row["tenure_end"] = b
            row["team_games_in_window"] = None
            row["confidence"] = "provisional_high" if not day_ambiguous else "review"
            row["start_reason"] = "multi_stint_segment_start"
            row["end_reason"] = "multi_stint_segment_end"
            row["same_day_resolution"] = "requires schedule/time audit" if day_ambiguous else "no same-day in/out collision"
            flags = list(dict.fromkeys(
                list(row.get("audit_flags") or [])
                + ["multi_stint_segment", f"segment_{idx}_of_{len(segments)}"]
            ))
            if day_ambiguous:
                flags.append("same_day_reacquisition_collision")
            row["audit_flags"] = flags
            row["segment_index"] = idx
            row["segment_count"] = len(segments)
            row["segment_source_references"] = sorted(set(refs))
            output.append(row)

    output.sort(key=lambda w: (
        w["season"], int(str(w["player_id"])), int(w["team_id"]),
        w["tenure_start"], w["tenure_end"],
    ))
    with gzip.open(WINDOWS, "wt", encoding="utf-8") as f:
        for row in output:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "input_windows": len(windows),
        "output_windows": len(output),
        "player_team_seasons_split": split_keys,
        "extra_segments_created": extra_segments,
        "same_day_in_out_collisions": ambiguous_same_day,
        "historical_event_dates_normalized": normalized_date_events,
        "unparseable_event_dates": 0,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
