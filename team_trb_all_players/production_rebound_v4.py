#!/usr/bin/env python3
"""Production rebound layer v4: v3 plus finite order-preserving repair evidence.

This does NOT widen any matcher. It starts from production_rebound_v3 and only
fills a still-unmatched PBP rebound when a committed evidence record identifies
one exact PBP row and one exact NBA rebound event. Every identifying field,
lineup, elapsed time, real/placeholder status, and event non-reuse is asserted
at runtime. The locked legacy classifier remains authoritative downstream.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import pandas as pd

import local_treb_rebuild as core
import production_rebound_v3 as base

_EVIDENCE_PATH = Path(__file__).resolve().parent / "final_integrity_rebuild" / "rebound_forensics" / "ORDER_PRESERVING_REBOUND_REPAIRS.json"


def _load_repairs() -> dict[int, list[dict]]:
    data=json.loads(_EVIDENCE_PATH.read_text())
    assert data.get("status")=="PROMOTED", data.get("status")
    assert int(data.get("repair_games",0))==33, data.get("repair_games")
    assert int(data.get("repair_rows",0))==33, data.get("repair_rows")
    assert int(data.get("control_wrong",-1))==0, data.get("control_wrong")
    out:dict[int,list[dict]]={}
    for rec in data.get("repairs",[]):
        out.setdefault(int(rec["game_id"]),[]).append(rec)
    assert len(out)==33, len(out)
    return out


def _norm(value: object) -> str:
    if pd.isna(value): return ""
    return re.sub(r"\s+"," ",str(value)).strip().lower()


def _pbp_rebounds(pbp_game:pd.DataFrame) -> pd.DataFrame:
    ordered=pbp_game.copy()
    ordered["PREV_PBP_DESCRIPTION"]=ordered.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    rows=ordered[ordered.DESCRIPTION.fillna("").str.contains("rebound",case=False)].copy()
    rows["DESCRIPTION_NORM"]=rows.DESCRIPTION.map(_norm)
    rows["START_ELAPSED"]=[core.elapsed_seconds(int(p),c) for p,c in zip(rows.PERIOD,rows.STARTTIME)]
    rows["END_ELAPSED"]=[core.elapsed_seconds(int(p),c) for p,c in zip(rows.PERIOD,rows.ENDTIME)]
    return rows


def join_pbp_rebounds(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha:int=5) -> tuple[pd.DataFrame,dict]:
    joined,audit=base.join_pbp_rebounds(lineups,pbp_game,alpha=alpha)
    game_id=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    repairs=_load_repairs().get(game_id,[])
    if not repairs:
        audit["order_preserving_repairs"]=0
        return joined,audit

    nba=lineups.events
    rows=_pbp_rebounds(pbp_game)
    used=set(pd.to_numeric(joined.NBA_INDEX,errors="coerce").dropna().astype(int))
    additions=[]; applied=[]

    for rec in repairs:
        period=int(rec["period"]); start=str(rec["start_time"]); end=str(rec["end_time"]); desc=str(rec["pbp_description"])
        hit=rows[
            rows.PERIOD.eq(period) &
            rows.STARTTIME.astype(str).eq(start) &
            rows.ENDTIME.astype(str).eq(end) &
            rows.DESCRIPTION.astype(str).eq(desc)
        ]
        if len(hit)!=1:
            raise ValueError(f"order-preserving evidence PBP identity mismatch game={game_id} period={period} start={start} end={end} desc={desc!r} hits={len(hit)}")
        pbp_idx=hit.index[0]
        if pbp_idx in joined.index:
            raise ValueError(f"order-preserving evidence row already matched by base v3 game={game_id} pbp_index={pbp_idx}")

        eventnum=int(rec["nba_eventnum"])
        nhit=nba[nba.PERIOD.eq(period)&nba.EVENTNUM.eq(eventnum)]
        if len(nhit)!=1:
            raise ValueError(f"order-preserving evidence NBA identity mismatch game={game_id} period={period} eventnum={eventnum} hits={len(nhit)}")
        ni=int(nhit.index[0])
        if ni in used:
            raise ValueError(f"order-preserving evidence would reuse NBA event game={game_id} eventnum={eventnum}")
        if int(nba.loc[ni,"EVENTMSGTYPE"])!=4:
            raise ValueError(f"order-preserving evidence target is not rebound game={game_id} eventnum={eventnum}")
        elapsed=int(nba.loc[ni,"ELAPSED"])
        if elapsed!=int(rec["nba_elapsed"]):
            raise ValueError(f"order-preserving elapsed drift game={game_id} eventnum={eventnum} observed={elapsed} expected={rec['nba_elapsed']}")
        lineup=[int(x) for x in nba.loc[ni,"LINEUP"]]
        if lineup!=[int(x) for x in rec["lineup"]]:
            raise ValueError(f"order-preserving lineup drift game={game_id} eventnum={eventnum}")
        real=bool(core._nba_real_rebound(nba,ni))
        if real!=bool(rec["real"]):
            raise ValueError(f"order-preserving real-status drift game={game_id} eventnum={eventnum}")

        add=hit.copy()
        add["NBA_INDEX"]=ni
        add["LINEUP"]=pd.Series([nba.loc[ni,"LINEUP"]],index=add.index,dtype=object)
        for column in ("EVENTMSGTYPE","EVENTMSGACTIONTYPE","PLAYER1_ID","ELAPSED","EVENTNUM"):
            add["NBA_"+column]=nba.loc[ni,column]
        add["NBA_IS_REAL_REBOUND"]=real
        additions.append(add); used.add(ni)
        applied.append({"period":period,"start_time":start,"end_time":end,"pbp_description":desc,"nba_eventnum":eventnum,"nba_elapsed":elapsed,"method":"unique_order_preserving_assignment"})

    if additions:
        joined=pd.concat([joined,*additions],axis=0).sort_index(kind="stable")

    # Recompute the fatal unmatched audit directly from exact row identity.
    matched_indices=set(joined.index)
    remaining=[]
    for idx,row in rows.iterrows():
        if idx not in matched_indices:
            remaining.append({"game_id":game_id,"period":int(row.PERIOD),"start_time":str(row.STARTTIME),"end_time":str(row.ENDTIME),"description":str(row.DESCRIPTION)})
    audit=dict(audit)
    audit["matched_rebound_bearing_rows"]=int(len(joined))
    audit["unmatched_rebound_bearing_rows"]=int(len(remaining))
    audit["unmatched_rows"]=remaining
    audit["order_preserving_repairs"]=int(len(applied))
    audit["order_preserving_records"]=applied
    return joined,audit


def classify_rebounds(pbp_game:pd.DataFrame) -> pd.DataFrame:
    return base.classify_rebounds(pbp_game)
