#!/usr/bin/env python3
"""Build a reusable forensic feature cache for historical rebound attribution audits.

This is diagnostic infrastructure only.  It reconstructs each target game once and
persists the evidence repeatedly used by later rule audits: lineup anchors,
live/dead predicates, rebounder resolution, nearby NBA events, and PBP possession
context.  Future hypothesis sweeps can operate on this cache without re-downloading
and reconstructing the raw historical sources.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v9 as rebound
import production_treb_engine_v3 as lineup_engine
import rebound_v5_source_only_audit as audit
import run_local_treb_production as io


def clean_scalar(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return None if math.isnan(v) else float(v)
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if hasattr(v, "item"):
        try:
            return clean_scalar(v.item())
        except Exception:
            pass
    return str(v)


def getv(row, name, default=None):
    try:
        return row[name] if name in row.index else default
    except Exception:
        return default


def lineup_json(v):
    if v is None:
        return None
    return [int(x) for x in v]


def nearby_events(events: pd.DataFrame, row, exclude=None):
    period = int(row.PERIOD)
    lo = min(int(row.START_ELAPSED), int(row.END_ELAPSED)) - 6
    hi = max(int(row.START_ELAPSED), int(row.END_ELAPSED)) + 6
    x = events[
        events.PERIOD.eq(period)
        & events.ELAPSED.ge(lo)
        & events.ELAPSED.le(hi)
    ].sort_values(["ELAPSED", "EVENTNUM"], kind="stable")
    out = []
    for idx, r in x.iterrows():
        rec = {
            "nba_index": int(idx),
            "excluded_matched_event": bool(exclude is not None and int(idx) == int(exclude)),
            "eventnum": clean_scalar(getv(r, "EVENTNUM")),
            "eventmsgtype": clean_scalar(getv(r, "EVENTMSGTYPE")),
            "eventmsgactiontype": clean_scalar(getv(r, "EVENTMSGACTIONTYPE")),
            "elapsed": clean_scalar(getv(r, "ELAPSED")),
            "player1_id": clean_scalar(getv(r, "PLAYER1_ID")),
            "description_norm": clean_scalar(getv(r, "DESCRIPTION_NORM")),
            "lineup": lineup_json(getv(r, "LINEUP")),
        }
        if rec["eventmsgtype"] == 4:
            rec["real_rebound"] = bool(core._nba_real_rebound(events, int(idx)))
        else:
            rec["real_rebound"] = None
        out.append(rec)
    return out


def possession_context(pbp_game: pd.DataFrame, row):
    pid_col = core.POSSESSION_ID
    pid = getv(row, pid_col)
    if pid_col not in pbp_game.columns or pd.isna(pid):
        return []
    x = pbp_game[pbp_game[pid_col].eq(pid)]
    out = []
    for idx, r in x.iterrows():
        out.append({
            "pbp_index": int(idx),
            "period": clean_scalar(getv(r, "PERIOD")),
            "start_time": clean_scalar(getv(r, "STARTTIME")),
            "end_time": clean_scalar(getv(r, "ENDTIME")),
            "description": clean_scalar(getv(r, "DESCRIPTION")),
            "offensive_rebounds": clean_scalar(getv(r, "OFFENSIVEREBOUNDS")),
        })
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--games", required=True)
    p.add_argument("--chunk-id", required=True)
    p.add_argument("--nba", type=Path, required=True)
    p.add_argument("--v3", type=Path, required=True)
    p.add_argument("--pbp", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    z = p.parse_args()

    ids = [int(x) for x in z.games.split(",") if x]
    nba = io.normalize_nba(pd.read_csv(z.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(z.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(z.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}

    records = []
    game_summaries = []
    for gid in ids:
        lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
        events = lu.events
        joined, _ = rebound.join_pbp_rebounds(lu, pg[gid])
        rows = audit.rows_for_game(pg[gid])
        rmap = audit.rebounder_map(events)
        matched_count = 0
        residual_count = 0
        for idx, row in rows.iterrows():
            matched = idx in joined.index and pd.notna(joined.loc[idx, "NBA_INDEX"])
            ni = int(joined.loc[idx, "NBA_INDEX"]) if matched else None
            if matched:
                matched_count += 1
            else:
                residual_count += 1
            lp = audit.lineup_predictions(events, row, exclude=ni)
            rp = audit.live_predictions(row, rmap)
            rec = {
                "chunk_id": z.chunk_id,
                "year": int(z.year),
                "game_id": int(gid),
                "pbp_index": int(idx),
                "period": int(row.PERIOD),
                "start_time": str(row.STARTTIME),
                "end_time": str(row.ENDTIME),
                "start_elapsed": int(row.START_ELAPSED),
                "end_elapsed": int(row.END_ELAPSED),
                "description": str(row.DESCRIPTION),
                "previous_pbp_description": clean_scalar(getv(row, "PREV_PBP_DESCRIPTION")),
                "possession_id": clean_scalar(getv(row, core.POSSESSION_ID)),
                "rebound_number": clean_scalar(getv(row, "REBOUND_NUMBER")),
                "offensive_rebounds": clean_scalar(getv(row, "OFFENSIVEREBOUNDS")),
                "pbp_is_oreb": bool(row.PBP_IS_OREB),
                "counter_format": bool(audit.COUNTER_RE.search(str(row.DESCRIPTION))),
                "bracket_format": bool(audit.BRACKET_RE.match(str(row.DESCRIPTION))),
                "name_key": audit.name_key(row.DESCRIPTION),
                "resolved_player_id": clean_scalar(rmap.get(audit.name_key(row.DESCRIPTION))),
                "matched": bool(matched),
                "nba_index": ni,
                "actual_real_rebound": bool(core._nba_real_rebound(events, ni)) if matched else None,
                "actual_lineup": lineup_json(events.loc[ni, "LINEUP"]) if matched else None,
                "lineup_predictions": {k: lineup_json(v) for k, v in lp.items()},
                "live_predictions": {k: clean_scalar(v) for k, v in rp.items()},
                "nearby_nba_events": nearby_events(events, row, exclude=ni),
                "pbp_possession_context": possession_context(pg[gid], row),
            }
            records.append(rec)
        game_summaries.append({
            "game_id": int(gid),
            "pbp_rebound_rows": int(len(rows)),
            "matched_rows": int(matched_count),
            "unmatched_rows": int(residual_count),
        })

    payload = {
        "status": "FORENSIC_FEATURE_CACHE",
        "schema_version": 1,
        "engine": "production_rebound_v9",
        "chunk_id": z.chunk_id,
        "year": int(z.year),
        "games": ids,
        "game_summaries": game_summaries,
        "record_count": len(records),
        "records": records,
    }
    z.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(z.output, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(json.dumps({
        "chunk_id": z.chunk_id,
        "year": z.year,
        "games": len(ids),
        "records": len(records),
        "unmatched": sum(x["unmatched_rows"] for x in game_summaries),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
