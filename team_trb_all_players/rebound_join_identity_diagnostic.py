#!/usr/bin/env python3
"""Forensic diagnostic for accepted PBP->NBA rebound joins that disagree on rebounder identity.

This does not repair or promote anything.  It records, for one known control row,
the current production match plus every nearby NBA event/rebound candidate and
nearby PBP rebound row so a subsequent rule can be derived from explicit evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v4 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io


def norm(v: object) -> str:
    return re.sub(r"\s+", " ", "" if pd.isna(v) else str(v)).strip().lower()


def name_key(v: object) -> str:
    return norm(v).split(" rebound", 1)[0].strip()


def pbp_rebounds(pbp_game: pd.DataFrame) -> pd.DataFrame:
    x = pbp_game.copy()
    x["PREV_PBP_DESCRIPTION"] = x.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    r = x[x.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    r["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.STARTTIME)]
    r["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.ENDTIME)]
    r["DESCRIPTION_NORM"] = r.DESCRIPTION.map(norm)
    return r


def unique_rebounder_map(events: pd.DataFrame) -> dict[str, int]:
    d: dict[str, set[int]] = {}
    for _, r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid = int(r.PLAYER1_ID)
        k = name_key(r.DESCRIPTION_NORM)
        if 0 < pid < core.PLAYER_MAX and k:
            d.setdefault(k, set()).add(pid)
    return {k: next(iter(v)) for k, v in d.items() if len(v) == 1}


def serial(v):
    if isinstance(v, (pd._libs.missing.NAType,)) or pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--game-id", type=int, required=True)
    ap.add_argument("--pbp-index", type=int, required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    nba_all = io.normalize_nba(pd.read_csv(a.nba, low_memory=False))
    v3_all = lineup_engine.normalize_v3(pd.read_csv(a.v3, low_memory=False))
    pbp_all = io.normalize_pbp(pd.read_csv(a.pbp, low_memory=False))
    nba_game = nba_all[nba_all.GAME_ID.eq(a.game_id)].copy()
    v3_game = v3_all[v3_all.gameId.eq(a.game_id)].copy()
    pbp_game = pbp_all[pbp_all.GAMEID.eq(a.game_id)].copy()
    assert not nba_game.empty and not v3_game.empty and not pbp_game.empty

    lu = lineup_engine.reconstruct_game_lineups(nba_game, v3_game)
    events = lu.events
    joined, audit = rebound.join_pbp_rebounds(lu, pbp_game)
    rows = pbp_rebounds(pbp_game)
    assert a.pbp_index in rows.index, (a.game_id, a.pbp_index, list(rows.index[:5]))
    row = rows.loc[a.pbp_index]
    rmap = unique_rebounder_map(events)
    resolved_rid = rmap.get(name_key(row.DESCRIPTION))

    current_nba_index = None
    if a.pbp_index in joined.index and pd.notna(joined.loc[a.pbp_index, "NBA_INDEX"]):
        current_nba_index = int(joined.loc[a.pbp_index, "NBA_INDEX"])

    alpha = 5
    start = int(row.START_ELAPSED)
    end = int(row.END_ELAPSED)
    lo = min(start, end) - 15
    hi = max(start, end) + 15
    legal_lo = start - alpha
    legal_hi = end + alpha
    nearby = events[events.PERIOD.eq(int(row.PERIOD)) & events.ELAPSED.ge(lo) & events.ELAPSED.le(hi)].copy()

    candidates = []
    for ni, ev in nearby.sort_values(["ELAPSED", "EVENTNUM"], kind="stable").iterrows():
        ni = int(ni)
        desc = str(ev.DESCRIPTION_NORM)
        is_rebound = int(ev.EVENTMSGTYPE) == 4
        pid = int(ev.PLAYER1_ID)
        candidates.append({
            "nba_index": ni,
            "eventnum": int(ev.EVENTNUM),
            "elapsed": int(ev.ELAPSED),
            "event_type": int(ev.EVENTMSGTYPE),
            "action_type": int(ev.EVENTMSGACTIONTYPE),
            "player1_id": pid,
            "description": desc,
            "name_key": name_key(desc),
            "is_rebound": is_rebound,
            "is_real_rebound": bool(core._nba_real_rebound(events, ni)) if is_rebound else None,
            "description_distance": float(core._distance(norm(row.DESCRIPTION), desc)),
            "same_resolved_rebounder": bool(resolved_rid is not None and pid == int(resolved_rid)),
            "in_current_legal_window": bool(int(ev.ELAPSED) > legal_lo and int(ev.ELAPSED) < legal_hi),
            "is_current_match": bool(current_nba_index == ni),
            "lineup": [int(x) for x in ev.LINEUP],
        })

    legal_rebounds = [x for x in candidates if x["is_rebound"] and x["in_current_legal_window"]]
    semantic_legal = [x for x in legal_rebounds if x["same_resolved_rebounder"]]
    exact_desc_legal = [x for x in legal_rebounds if x["description"] == norm(row.DESCRIPTION)]

    poss = row[core.POSSESSION_ID]
    near_pbp = rows[(rows.PERIOD.eq(int(row.PERIOD))) & (rows[core.POSSESSION_ID].eq(poss))].copy()
    pbp_context = []
    for idx, rr in near_pbp.iterrows():
        pbp_context.append({
            "pbp_index": int(idx),
            "start_time": str(rr.STARTTIME),
            "end_time": str(rr.ENDTIME),
            "start_elapsed": int(rr.START_ELAPSED),
            "end_elapsed": int(rr.END_ELAPSED),
            "description": str(rr.DESCRIPTION),
            "previous_description": "" if pd.isna(rr.PREV_PBP_DESCRIPTION) else str(rr.PREV_PBP_DESCRIPTION),
            "is_target": int(idx) == a.pbp_index,
        })

    current = next((x for x in candidates if x["is_current_match"]), None)
    out = {
        "status": "DIAGNOSTIC_ONLY",
        "year": a.year,
        "game_id": a.game_id,
        "pbp_index": a.pbp_index,
        "period": int(row.PERIOD),
        "start_time": str(row.STARTTIME),
        "end_time": str(row.ENDTIME),
        "start_elapsed": start,
        "end_elapsed": end,
        "description": str(row.DESCRIPTION),
        "previous_description": "" if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),
        "resolved_rebounder_id": int(resolved_rid) if resolved_rid is not None else None,
        "current_nba_index": current_nba_index,
        "current_match": current,
        "legal_rebound_candidates": legal_rebounds,
        "semantic_legal_candidates": semantic_legal,
        "exact_description_legal_candidates": exact_desc_legal,
        "nearby_nba_events": candidates,
        "same_possession_pbp_rebounds": pbp_context,
        "join_audit_counts": {k: serial(v) for k, v in audit.items() if isinstance(v, (int, float, str, bool)) or v is None},
        "diagnostic_flags": {
            "current_match_rebounder_conflicts_with_pbp": bool(current and resolved_rid is not None and int(current["player1_id"]) != int(resolved_rid)),
            "unique_semantic_rebound_candidate_in_legal_window": len(semantic_legal) == 1,
            "unique_exact_description_rebound_candidate_in_legal_window": len(exact_desc_legal) == 1,
            "semantic_candidate_differs_from_current": bool(len(semantic_legal) == 1 and current_nba_index != int(semantic_legal[0]["nba_index"])),
        },
    }
    a.output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({
        "game_id": a.game_id,
        "pbp_index": a.pbp_index,
        "resolved_rebounder_id": out["resolved_rebounder_id"],
        "current_eventnum": current["eventnum"] if current else None,
        "current_player1_id": current["player1_id"] if current else None,
        "legal_rebounds": len(legal_rebounds),
        "semantic_legal": [x["eventnum"] for x in semantic_legal],
        "exact_desc_legal": [x["eventnum"] for x in exact_desc_legal],
        "flags": out["diagnostic_flags"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
