#!/usr/bin/env python3
"""Census accepted PBP->NBA rebound joins for semantic rebounder conflicts.

Diagnostic only.  No match is altered.  For each already-accepted rebound join in
the forensic games, compare the current NBA PLAYER1_ID with the game-local unique
rebounder ID implied by the PBP rebound description, and enumerate legal rebound
candidates that satisfy that identity.
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


def make_rows(pbp_game: pd.DataFrame) -> pd.DataFrame:
    x = pbp_game.copy()
    x["PREV_PBP_DESCRIPTION"] = x.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    r = x[x.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    r["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.STARTTIME)]
    r["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.ENDTIME)]
    r["DESCRIPTION_NORM"] = r.DESCRIPTION.map(norm)
    return r


def rebounder_map(events: pd.DataFrame) -> dict[str, int]:
    d: dict[str, set[int]] = {}
    for _, r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid = int(r.PLAYER1_ID)
        k = name_key(r.DESCRIPTION_NORM)
        if 0 < pid < core.PLAYER_MAX and k:
            d.setdefault(k, set()).add(pid)
    return {k: next(iter(v)) for k, v in d.items() if len(v) == 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--chunk-id", required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    game_ids = [int(x) for x in a.games.split(",") if x]
    nba = io.normalize_nba(pd.read_csv(a.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(a.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(a.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}

    counts = {
        "games": 0,
        "matched_rebound_rows": 0,
        "resolved_rebounder_rows": 0,
        "current_identity_agrees": 0,
        "current_identity_conflicts": 0,
        "conflict_unique_semantic_candidate": 0,
        "conflict_unique_semantic_candidate_unused_elsewhere": 0,
        "conflict_no_semantic_candidate": 0,
        "conflict_multiple_semantic_candidates": 0,
        "current_match_not_rebound_event": 0,
    }
    conflicts = []

    for gid in game_ids:
        if gid not in ng or gid not in vg or gid not in pg:
            raise KeyError(f"missing source for game {gid}")
        counts["games"] += 1
        lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
        events = lu.events
        joined, _ = rebound.join_pbp_rebounds(lu, pg[gid])
        rows = make_rows(pg[gid])
        rmap = rebounder_map(events)

        used_by: dict[int, list[int]] = {}
        for pbp_idx, jr in joined.iterrows():
            if pd.notna(jr.NBA_INDEX):
                used_by.setdefault(int(jr.NBA_INDEX), []).append(int(pbp_idx))

        for idx, row in rows.iterrows():
            if idx not in joined.index or pd.isna(joined.loc[idx, "NBA_INDEX"]):
                continue
            counts["matched_rebound_rows"] += 1
            ni = int(joined.loc[idx, "NBA_INDEX"])
            current_pid = int(events.loc[ni, "PLAYER1_ID"])
            current_type = int(events.loc[ni, "EVENTMSGTYPE"])
            if current_type != 4:
                counts["current_match_not_rebound_event"] += 1
            rid = rmap.get(name_key(row.DESCRIPTION))
            if rid is None:
                continue
            counts["resolved_rebounder_rows"] += 1
            if int(rid) == current_pid:
                counts["current_identity_agrees"] += 1
                continue

            counts["current_identity_conflicts"] += 1
            alpha = 5
            candidates = events[
                events.PERIOD.eq(int(row.PERIOD))
                & events.EVENTMSGTYPE.eq(4)
                & events.ELAPSED.gt(int(row.START_ELAPSED) - alpha)
                & events.ELAPSED.lt(int(row.END_ELAPSED) + alpha)
                & events.PLAYER1_ID.eq(int(rid))
            ].copy()
            sem = []
            for ci, ev in candidates.sort_values(["ELAPSED", "EVENTNUM"], kind="stable").iterrows():
                ci = int(ci)
                sem.append({
                    "nba_index": ci,
                    "eventnum": int(ev.EVENTNUM),
                    "elapsed": int(ev.ELAPSED),
                    "player1_id": int(ev.PLAYER1_ID),
                    "description": str(ev.DESCRIPTION_NORM),
                    "description_distance": float(core._distance(row.DESCRIPTION_NORM, ev.DESCRIPTION_NORM)),
                    "is_real_rebound": bool(core._nba_real_rebound(events, ci)),
                    "used_by_pbp_indices": used_by.get(ci, []),
                    "used_by_other_pbp": any(int(x) != int(idx) for x in used_by.get(ci, [])),
                    "lineup": [int(x) for x in ev.LINEUP],
                })

            if len(sem) == 0:
                counts["conflict_no_semantic_candidate"] += 1
            elif len(sem) == 1:
                counts["conflict_unique_semantic_candidate"] += 1
                if not sem[0]["used_by_other_pbp"]:
                    counts["conflict_unique_semantic_candidate_unused_elsewhere"] += 1
            else:
                counts["conflict_multiple_semantic_candidates"] += 1

            conflicts.append({
                "game_id": gid,
                "pbp_index": int(idx),
                "period": int(row.PERIOD),
                "start_time": str(row.STARTTIME),
                "end_time": str(row.ENDTIME),
                "description": str(row.DESCRIPTION),
                "resolved_rebounder_id": int(rid),
                "current_nba_index": ni,
                "current_eventnum": int(events.loc[ni, "EVENTNUM"]),
                "current_elapsed": int(events.loc[ni, "ELAPSED"]),
                "current_event_type": current_type,
                "current_player1_id": current_pid,
                "current_description": str(events.loc[ni, "DESCRIPTION_NORM"]),
                "current_description_distance": float(core._distance(row.DESCRIPTION_NORM, events.loc[ni, "DESCRIPTION_NORM"])),
                "current_used_by_pbp_indices": used_by.get(ni, []),
                "semantic_candidates": sem,
            })

    out = {
        "status": "DIAGNOSTIC_ONLY",
        "chunk_id": a.chunk_id,
        "year": a.year,
        "counts": counts,
        "conflicts": conflicts,
    }
    a.output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"chunk_id": a.chunk_id, "year": a.year, "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
