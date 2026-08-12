#!/usr/bin/env python3
"""Audit a strict direct-player rebound identity rule.

Diagnostic only.  It deliberately avoids PBP possession row ordering and prior-shot
inference.  For a PBP rebound whose player name maps uniquely to one NBA rebounder
ID in the game, consider only NBA rebound events by that exact player inside the
PBP row's legal time interval.  A row is applicable only when exactly one such
candidate exists.

Matched production rows are controls.  Residual candidates additionally must be
unused by every already matched PBP rebound row.  No repair is promoted here.
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
    r = pbp_game[pbp_game.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    r["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.STARTTIME)]
    r["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.ENDTIME)]
    return r


def exact_rebounder_map(events: pd.DataFrame) -> dict[str, int]:
    d: dict[str, set[int]] = {}
    for _, r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid = int(r.PLAYER1_ID)
        key = name_key(r.DESCRIPTION_NORM)
        if 0 < pid < core.PLAYER_MAX and key:
            d.setdefault(key, set()).add(pid)
    return {k: next(iter(v)) for k, v in d.items() if len(v) == 1}


def candidates(events: pd.DataFrame, row: pd.Series, rid: int, alpha: int = 5) -> pd.DataFrame:
    lo = min(int(row.START_ELAPSED), int(row.END_ELAPSED)) - alpha
    hi = max(int(row.START_ELAPSED), int(row.END_ELAPSED)) + alpha
    return events[
        events.PERIOD.eq(int(row.PERIOD))
        & events.EVENTMSGTYPE.eq(4)
        & events.ELAPSED.gt(lo)
        & events.ELAPSED.lt(hi)
        & events.PLAYER1_ID.eq(int(rid))
    ].copy()


def event_record(events: pd.DataFrame, idx: int) -> dict:
    r = events.loc[idx]
    return {
        "nba_index": int(idx),
        "eventnum": int(r.EVENTNUM),
        "elapsed": int(r.ELAPSED),
        "player1_id": int(r.PLAYER1_ID),
        "description": str(r.DESCRIPTION_NORM),
        "lineup": [int(x) for x in r.LINEUP],
        "real": bool(core._nba_real_rebound(events, int(idx))),
    }


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

    ids = [int(x) for x in a.games.split(",") if x]
    nba = io.normalize_nba(pd.read_csv(a.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(a.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(a.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}

    c = {
        "resolved_controls": 0,
        "unique_candidate_controls": 0,
        "identity_correct": 0,
        "identity_wrong": 0,
        "zero_candidate_controls": 0,
        "multiple_candidate_controls": 0,
        "source_disagreement_controls": 0,
        "source_disagreement_with_candidate": 0,
        "residual_rows": 0,
        "resolved_residual_rows": 0,
        "unique_candidate_residual_rows": 0,
        "unique_unused_candidate_residual_rows": 0,
        "candidate_real_true": 0,
        "candidate_real_false": 0,
    }
    wrong = []
    residual = []

    for gid in ids:
        if gid not in ng or gid not in vg or gid not in pg:
            raise KeyError(f"missing source game={gid}")
        lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
        events = lu.events
        joined, _ = rebound.join_pbp_rebounds(lu, pg[gid])
        rows = make_rows(pg[gid])
        rmap = exact_rebounder_map(events)
        used = {int(x) for x in pd.to_numeric(joined.NBA_INDEX, errors="coerce").dropna().astype(int)}

        for idx, row in rows.iterrows():
            rid = rmap.get(name_key(row.DESCRIPTION))
            matched = idx in joined.index and pd.notna(joined.loc[idx, "NBA_INDEX"])
            if matched:
                if rid is None:
                    continue
                c["resolved_controls"] += 1
                actual = int(joined.loc[idx, "NBA_INDEX"])
                actual_pid = int(events.loc[actual, "PLAYER1_ID"])
                cand = candidates(events, row, rid)
                if actual_pid != int(rid):
                    c["source_disagreement_controls"] += 1
                    if len(cand):
                        c["source_disagreement_with_candidate"] += 1
                if len(cand) == 0:
                    c["zero_candidate_controls"] += 1
                    continue
                if len(cand) != 1:
                    c["multiple_candidate_controls"] += 1
                    continue
                c["unique_candidate_controls"] += 1
                pred = int(cand.index[0])
                if pred == actual:
                    c["identity_correct"] += 1
                else:
                    c["identity_wrong"] += 1
                    wrong.append({
                        "game_id": gid,
                        "pbp_index": int(idx),
                        "description": str(row.DESCRIPTION),
                        "resolved_rebounder_id": int(rid),
                        "actual": event_record(events, actual),
                        "predicted": event_record(events, pred),
                    })
                continue

            if idx in joined.index:
                # Defensive: synthetic matched row with no NBA_INDEX is not residual.
                continue
            c["residual_rows"] += 1
            rec = {
                "game_id": gid,
                "pbp_index": int(idx),
                "period": int(row.PERIOD),
                "start_time": str(row.STARTTIME),
                "end_time": str(row.ENDTIME),
                "description": str(row.DESCRIPTION),
                "resolved_rebounder_id": None if rid is None else int(rid),
                "candidate_count": 0,
                "candidate_unused": False,
                "candidate": None,
            }
            if rid is not None:
                c["resolved_residual_rows"] += 1
                cand = candidates(events, row, rid)
                rec["candidate_count"] = int(len(cand))
                if len(cand) == 1:
                    c["unique_candidate_residual_rows"] += 1
                    ni = int(cand.index[0])
                    unused = ni not in used
                    rec["candidate_unused"] = bool(unused)
                    rec["candidate"] = event_record(events, ni)
                    if unused:
                        c["unique_unused_candidate_residual_rows"] += 1
                        if rec["candidate"]["real"]:
                            c["candidate_real_true"] += 1
                        else:
                            c["candidate_real_false"] += 1
            residual.append(rec)

    out = {
        "status": "DIAGNOSTIC_ONLY",
        "chunk_id": a.chunk_id,
        "year": a.year,
        "controls": c,
        "wrong_records": wrong,
        "residual_rows": residual,
    }
    a.output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"chunk_id": a.chunk_id, "year": a.year, "controls": c}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
