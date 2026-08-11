#!/usr/bin/env python3
"""Diagnose TREB legacy repair queues without changing production decisions.

For each unmatched PBP rebound, report the best NBA same-period description
matches and whether the best match lies outside the source-compatible clock
window. For starter failures, compare the current candidate set with the
upstream nba-on-court candidate logic. For lineup/substitution failures, emit
nearby NBA events.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


def _source_candidates(period: pd.DataFrame, core, role_corrected: bool) -> list[int]:
    df = period.copy()
    # nba-on-court converts the remaining clock to elapsed seconds first.
    elapsed = pd.Series([core.elapsed_seconds(int(p), c) for p, c in zip(df.PERIOD, df.PCTIMESTRING)], index=df.index)
    df["_ELAPSED"] = elapsed

    def valid_name(col: str) -> pd.Series:
        return df[col].notna() if col in df.columns else pd.Series(False, index=df.index)

    p1 = df.loc[(~df.EVENTMSGTYPE.isin([9, 18])) & (~df.PERSON1TYPE.isin([6, 7])) & valid_name("PLAYER1_NAME"), "PLAYER1_ID"].unique()
    if role_corrected:
        p2_col = "PLAYER2_ID"
    else:
        # Exact behavior in the upstream package currently published on GitHub.
        p2_col = "PLAYER1_ID"
    p2 = df.loc[(~df.EVENTMSGTYPE.isin([9, 18])) & (~df.PERSON2TYPE.isin([6, 7])) & valid_name("PLAYER2_NAME"), p2_col].unique()
    p3 = df.loc[(~df.EVENTMSGTYPE.isin([9, 18])) & (~df.PERSON3TYPE.isin([6, 7])) & valid_name("PLAYER3_NAME"), "PLAYER3_ID"].unique()
    all_id = np.unique(np.concatenate((p1, p2, p3)))
    all_id = all_id[(all_id != 0) & (all_id < 1610612737)]

    subs = df[df.EVENTMSGTYPE.eq(8)]
    sub_off = pd.to_numeric(subs.PLAYER1_ID, errors="coerce").dropna().astype(int).unique()
    sub_on = pd.to_numeric(subs.PLAYER2_ID, errors="coerce").dropna().astype(int).unique()
    all_id = all_id[~np.isin(all_id, sub_on[~np.isin(sub_on, sub_off)])]
    both = sub_on[np.isin(sub_on, sub_off)]
    for pid in both:
        on_rows = df[(df.EVENTMSGTYPE.eq(8)) & (df.PLAYER2_ID.eq(pid))]
        off_rows = df[(df.EVENTMSGTYPE.eq(8)) & (df.PLAYER1_ID.eq(pid))]
        if on_rows.empty or off_rows.empty:
            continue
        on = int(on_rows._ELAPSED.min())
        off = int(off_rows._ELAPSED.min())
        if off > on:
            all_id = all_id[all_id != pid]
        elif off == on:
            on_event = int(on_rows.EVENTNUM.min())
            off_event = int(off_rows.EVENTNUM.min())
            if off_event > on_event:
                all_id = all_id[all_id != pid]
    return sorted(int(x) for x in all_id)


def _current_candidates(period: pd.DataFrame, core) -> dict[int, list[int]]:
    player_team = core._player_team(period)
    participants = set(player_team)
    subs = period.loc[period.EVENTMSGTYPE.eq(8)].copy()
    subs["_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(subs.PERIOD, subs.PCTIMESTRING)]
    subs = subs.sort_values(["_ELAPSED", "EVENTNUM"], kind="stable")
    sub_out = set(pd.to_numeric(subs.PLAYER1_ID, errors="coerce").dropna().astype(int))
    sub_in = set(pd.to_numeric(subs.PLAYER2_ID, errors="coerce").dropna().astype(int))
    candidates = participants - (sub_in - sub_out)
    for pid in sub_in & sub_out:
        first = subs[(subs.PLAYER1_ID.eq(pid)) | (subs.PLAYER2_ID.eq(pid))].iloc[0]
        if int(first.PLAYER2_ID) == pid:
            candidates.discard(pid)
        else:
            candidates.add(pid)
    by_team: dict[int, list[int]] = {}
    for pid in candidates:
        team = player_team.get(pid)
        if team is not None:
            by_team.setdefault(int(team), []).append(int(pid))
    return {k: sorted(v) for k, v in by_team.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    root = args.repo_root.resolve()
    sys.path.insert(0, str(root / "team_trb_all_players"))
    import local_treb_rebuild as core
    from run_local_treb_production import normalize_nba, season_name

    season = season_name(args.year)
    repair_path = args.output / "repair_queue" / f"{season}.json"
    if not repair_path.exists():
        payload = {"season": season, "status": "NO_REPAIR_QUEUE"}
        out = args.output / f"diagnostic_{season}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n")
        print(json.dumps(payload))
        return 0

    q = json.loads(repair_path.read_text())
    nba_path = root / "team_trb_all_players" / "impact_database" / "local_raw" / f"nbastats_{args.year}.csv"
    nba = normalize_nba(pd.read_csv(nba_path, low_memory=False))
    nba["DESCRIPTION_NORM"] = core.nba_description(nba)
    nba["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(nba.PERIOD, nba.PCTIMESTRING)]

    unmatched_diagnostics = []
    category = Counter()
    for row in q.get("unmatched_rebound_rows", []):
        gid = int(row["game_id"])
        period_no = int(row["period"])
        desc = core.normalize_description(row["description"])
        start = core.elapsed_seconds(period_no, row["start_time"])
        end = core.elapsed_seconds(period_no, row["end_time"])
        lo, hi = start - 5, end + 5
        frame = nba[(nba.GAME_ID.eq(gid)) & (nba.PERIOD.eq(period_no))].copy()
        candidates = []
        for _, n in frame.iterrows():
            d = float(core._distance(desc, n.DESCRIPTION_NORM))
            elapsed = int(n.ELAPSED)
            within = bool(elapsed > lo and elapsed < hi)
            if elapsed <= lo:
                window_gap = lo - elapsed
            elif elapsed >= hi:
                window_gap = elapsed - hi
            else:
                window_gap = 0
            candidates.append({
                "event_num": int(n.EVENTNUM),
                "clock": str(n.PCTIMESTRING),
                "elapsed": elapsed,
                "description": str(n.DESCRIPTION_NORM),
                "distance": round(d, 6),
                "within_window": within,
                "window_gap_seconds": int(window_gap),
                "event_type": int(n.EVENTMSGTYPE),
                "action_type": int(n.EVENTMSGACTIONTYPE),
                "player1_id": int(n.PLAYER1_ID),
            })
        candidates.sort(key=lambda x: (x["distance"], x["window_gap_seconds"], x["event_num"]))
        top = candidates[:5]
        best = top[0] if top else None
        if best is None:
            cat = "NO_NBA_PERIOD_ROWS"
        elif best["distance"] < 0.2 and not best["within_window"]:
            cat = "STRONG_DESCRIPTION_MATCH_OUTSIDE_WINDOW"
        elif best["distance"] < 0.2 and best["within_window"]:
            cat = "STRONG_MATCH_INSIDE_WINDOW_NOT_SELECTED"
        elif best["distance"] >= 0.2:
            cat = "DESCRIPTION_MISMATCH_OR_MISSING_EVENT"
        else:
            cat = "OTHER"
        category[cat] += 1
        unmatched_diagnostics.append({**row, "category": cat, "clock_window_elapsed": [lo, hi], "top_nba_candidates": top})

    starter_diagnostics = []
    context_diagnostics = []
    starter_re = re.compile(r"(?:unresolved starters|unresolved carried starters) game=(\d+) period=(\d+) team=(\d+):")
    event_re = re.compile(r"game=(\d+) event=(\d+)")
    for exc in q.get("exceptions", []):
        err = str(exc.get("error", ""))
        m = starter_re.search(err)
        if m:
            gid, period_no, team_id = map(int, m.groups())
            period = nba[(nba.GAME_ID.eq(gid)) & (nba.PERIOD.eq(period_no))].copy()
            team_map = core._player_team(period)
            current = _current_candidates(period, core).get(team_id, [])
            exact_all = _source_candidates(period, core, role_corrected=False)
            corrected_all = _source_candidates(period, core, role_corrected=True)
            exact_team = sorted(pid for pid in exact_all if team_map.get(pid) == team_id)
            corrected_team = sorted(pid for pid in corrected_all if team_map.get(pid) == team_id)
            starter_diagnostics.append({
                "game_id": gid,
                "period": period_no,
                "team_id": team_id,
                "error": err,
                "current_team_candidates": current,
                "upstream_exact_team_candidates": exact_team,
                "upstream_role_corrected_team_candidates": corrected_team,
                "upstream_exact_all_candidates": exact_all,
                "upstream_role_corrected_all_candidates": corrected_all,
            })
            continue
        em = event_re.search(err)
        if em:
            gid, event_num = map(int, em.groups())
            frame = nba[(nba.GAME_ID.eq(gid)) & (nba.EVENTNUM.between(event_num - 6, event_num + 6))]
            context_diagnostics.append({
                "game_id": gid,
                "event_num": event_num,
                "error": err,
                "nearby_events": [
                    {
                        "event_num": int(r.EVENTNUM), "period": int(r.PERIOD), "clock": str(r.PCTIMESTRING),
                        "event_type": int(r.EVENTMSGTYPE), "action_type": int(r.EVENTMSGACTIONTYPE),
                        "player1_id": int(r.PLAYER1_ID), "player2_id": int(r.PLAYER2_ID),
                        "description": str(r.DESCRIPTION_NORM),
                    }
                    for _, r in frame.sort_values(["PERIOD", "EVENTNUM"]).iterrows()
                ],
            })

    repair_types = Counter(str(r.get("type")) for r in q.get("repairs", []))
    payload = {
        "season": season,
        "queue_exceptions": len(q.get("exceptions", [])),
        "queue_unmatched_rows": len(q.get("unmatched_rebound_rows", [])),
        "queue_repairs": len(q.get("repairs", [])),
        "repair_type_counts": dict(repair_types),
        "unmatched_category_counts": dict(category),
        "unmatched_diagnostics": unmatched_diagnostics,
        "starter_diagnostics": starter_diagnostics,
        "event_context_diagnostics": context_diagnostics,
    }
    out = args.output / f"diagnostic_{season}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({k: payload[k] for k in ["season", "queue_exceptions", "queue_unmatched_rows", "queue_repairs", "repair_type_counts", "unmatched_category_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
