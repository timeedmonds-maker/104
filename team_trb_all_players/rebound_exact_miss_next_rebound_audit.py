#!/usr/bin/env python3
"""Audit exact-missed-shot -> subsequent NBA rebound linkage.

This is diagnostic only.  It starts from an independently exact previous PBP miss
anchor and tests several deliberately narrow sequence rules against already
matched controls.  A rule is eligible for later promotion only if aggregate
controls show zero event-identity AND zero TREB-impact errors.
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

STRATEGIES = (
    "immediate_next_event_rebound",
    "first_rebound_before_new_play",
    "first_rebound_before_new_play_8s",
)


def norm(v: object) -> str:
    return re.sub(r"\s+", " ", "" if pd.isna(v) else str(v)).strip().lower()


def make_rows(pbp_game: pd.DataFrame) -> pd.DataFrame:
    x = pbp_game.copy()
    x["PREV_PBP_DESCRIPTION"] = x.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    r = x[x.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    r["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.STARTTIME)]
    r["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.ENDTIME)]
    return r


def exact_prior_miss(events: pd.DataFrame, row: pd.Series):
    prev = norm(row.PREV_PBP_DESCRIPTION)
    if not prev:
        return None
    h = events[
        events.PERIOD.eq(int(row.PERIOD))
        & events.DESCRIPTION_NORM.eq(prev)
        & events.EVENTMSGTYPE.isin([2, 3])
    ]
    # Retain the previously audited exact-anchor window.  This is an anchor
    # restriction, not the rebound-selection window.
    h = h[
        h.ELAPSED.ge(int(row.START_ELAPSED) - 5)
        & h.ELAPSED.le(int(row.END_ELAPSED) + 5)
    ]
    if len(h) != 1:
        return None
    i = int(h.index[0])
    return {
        "nba_index": i,
        "eventnum": int(events.loc[i, "EVENTNUM"]),
        "elapsed": int(events.loc[i, "ELAPSED"]),
        "description": str(events.loc[i, "DESCRIPTION_NORM"]),
        "shooter_id": int(events.loc[i, "PLAYER1_ID"]),
        "lineup": tuple(int(x) for x in events.loc[i, "LINEUP"]),
    }


def ordered_after(events: pd.DataFrame, miss: dict) -> pd.DataFrame:
    period = int(events.loc[miss["nba_index"], "PERIOD"])
    ev = events[events.PERIOD.eq(period)].sort_values(["EVENTNUM", "ELAPSED"], kind="stable")
    locs = [j for j, idx in enumerate(ev.index) if int(idx) == int(miss["nba_index"])]
    if len(locs) != 1:
        return ev.iloc[0:0]
    return ev.iloc[locs[0] + 1 :]


def choose(events: pd.DataFrame, miss: dict, strategy: str):
    aft = ordered_after(events, miss)
    if aft.empty:
        return None
    if strategy == "immediate_next_event_rebound":
        r = aft.iloc[0]
        return int(r.name) if int(r.EVENTMSGTYPE) == 4 else None

    # Any new shot/free-throw sequence, turnover, jump ball or period end means
    # the missed-shot rebound opportunity has ended.  Fouls, violations,
    # substitutions and timeouts may legitimately intervene in raw NBA PBP.
    barrier_types = {1, 2, 3, 5, 10, 13}
    max_seconds = 8 if strategy.endswith("_8s") else None
    for idx, r in aft.iterrows():
        dt = int(r.ELAPSED) - int(miss["elapsed"])
        if dt < 0:
            continue
        if max_seconds is not None and dt > max_seconds:
            return None
        typ = int(r.EVENTMSGTYPE)
        if typ == 4:
            return int(idx)
        if typ in barrier_types:
            return None
    return None


def player_team(nba_game: pd.DataFrame):
    return core._player_team(nba_game)


def event_kind(events: pd.DataFrame, idx: int, shooter_id: int, pteam: dict):
    rid = int(events.loc[idx, "PLAYER1_ID"])
    rt = pteam.get(rid)
    st = pteam.get(int(shooter_id))
    if rt is None or st is None:
        return None
    return "OREB" if int(rt) == int(st) else "DREB"


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

    counters = {
        s: {
            "anchor_applicable_controls": 0,
            "strategy_applicable_controls": 0,
            "identity_correct": 0,
            "identity_wrong": 0,
            "impact_correct": 0,
            "impact_wrong": 0,
            "residual_anchor_rows": 0,
            "residual_candidate_rows": 0,
        }
        for s in STRATEGIES
    }
    wrong = {s: [] for s in STRATEGIES}
    residual = []

    for gid in ids:
        if gid not in ng or gid not in vg or gid not in pg:
            raise KeyError(f"missing source game={gid}")
        lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
        events = lu.events
        joined, _ = rebound.join_pbp_rebounds(lu, pg[gid])
        rows = make_rows(pg[gid])
        pteam = player_team(ng[gid])

        for pbp_idx, row in rows.iterrows():
            miss = exact_prior_miss(events, row)
            matched = pbp_idx in joined.index and pd.notna(joined.loc[pbp_idx, "NBA_INDEX"])
            if matched:
                if miss is None:
                    continue
                actual_idx = int(joined.loc[pbp_idx, "NBA_INDEX"])
                if int(events.loc[actual_idx, "EVENTMSGTYPE"]) != 4:
                    continue
                actual_real = bool(core._nba_real_rebound(events, actual_idx))
                actual_kind = event_kind(events, actual_idx, miss["shooter_id"], pteam)
                actual_lineup = tuple(int(x) for x in events.loc[actual_idx, "LINEUP"])
                for s in STRATEGIES:
                    counters[s]["anchor_applicable_controls"] += 1
                    pred_idx = choose(events, miss, s)
                    if pred_idx is None:
                        continue
                    pred_kind = event_kind(events, pred_idx, miss["shooter_id"], pteam)
                    pred_real = bool(core._nba_real_rebound(events, pred_idx))
                    pred_lineup = tuple(int(x) for x in events.loc[pred_idx, "LINEUP"])
                    counters[s]["strategy_applicable_controls"] += 1
                    identity_good = pred_idx == actual_idx
                    if identity_good:
                        counters[s]["identity_correct"] += 1
                    else:
                        counters[s]["identity_wrong"] += 1
                    impact_good = (
                        pred_lineup == actual_lineup
                        and pred_real == actual_real
                        and pred_kind is not None
                        and actual_kind is not None
                        and pred_kind == actual_kind
                    )
                    if impact_good:
                        counters[s]["impact_correct"] += 1
                    else:
                        counters[s]["impact_wrong"] += 1
                    if not identity_good or not impact_good:
                        wrong[s].append({
                            "game_id": gid,
                            "pbp_index": int(pbp_idx),
                            "description": str(row.DESCRIPTION),
                            "previous_description": "" if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),
                            "miss_eventnum": miss["eventnum"],
                            "miss_elapsed": miss["elapsed"],
                            "actual_eventnum": int(events.loc[actual_idx, "EVENTNUM"]),
                            "actual_elapsed": int(events.loc[actual_idx, "ELAPSED"]),
                            "actual_player1_id": int(events.loc[actual_idx, "PLAYER1_ID"]),
                            "pred_eventnum": int(events.loc[pred_idx, "EVENTNUM"]),
                            "pred_elapsed": int(events.loc[pred_idx, "ELAPSED"]),
                            "pred_player1_id": int(events.loc[pred_idx, "PLAYER1_ID"]),
                            "actual_kind": actual_kind,
                            "pred_kind": pred_kind,
                            "identity_good": identity_good,
                            "impact_good": impact_good,
                        })
            elif pbp_idx not in joined.index:
                rec = {
                    "game_id": gid,
                    "pbp_index": int(pbp_idx),
                    "period": int(row.PERIOD),
                    "start_time": str(row.STARTTIME),
                    "end_time": str(row.ENDTIME),
                    "description": str(row.DESCRIPTION),
                    "previous_description": "" if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),
                    "anchor": None,
                    "strategies": {},
                }
                if miss is not None:
                    rec["anchor"] = {
                        "nba_index": miss["nba_index"],
                        "eventnum": miss["eventnum"],
                        "elapsed": miss["elapsed"],
                        "description": miss["description"],
                        "shooter_id": miss["shooter_id"],
                        "lineup": list(miss["lineup"]),
                    }
                for s in STRATEGIES:
                    if miss is not None:
                        counters[s]["residual_anchor_rows"] += 1
                    pred_idx = choose(events, miss, s) if miss is not None else None
                    if pred_idx is None:
                        rec["strategies"][s] = None
                        continue
                    counters[s]["residual_candidate_rows"] += 1
                    rec["strategies"][s] = {
                        "nba_index": pred_idx,
                        "eventnum": int(events.loc[pred_idx, "EVENTNUM"]),
                        "elapsed": int(events.loc[pred_idx, "ELAPSED"]),
                        "player1_id": int(events.loc[pred_idx, "PLAYER1_ID"]),
                        "description": str(events.loc[pred_idx, "DESCRIPTION_NORM"]),
                        "real": bool(core._nba_real_rebound(events, pred_idx)),
                        "kind": event_kind(events, pred_idx, miss["shooter_id"], pteam),
                        "lineup": [int(x) for x in events.loc[pred_idx, "LINEUP"]],
                    }
                residual.append(rec)

    out = {
        "status": "DIAGNOSTIC_ONLY",
        "chunk_id": a.chunk_id,
        "year": a.year,
        "strategies": counters,
        "wrong_records": wrong,
        "residual_rows": residual,
    }
    a.output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"chunk_id": a.chunk_id, "year": a.year, "strategies": counters, "residual_rows": len(residual)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
