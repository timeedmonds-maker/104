#!/usr/bin/env python3
"""Production rebound layer V7: V6 plus one control-proven named-player lineup repair."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import production_rebound_v6 as base

_EVIDENCE=Path(__file__).resolve().parent/'final_integrity_rebuild'/'rebound_forensics'/'V7_NAMED_PLAYER_REPAIR.json'

def _load():
    d=json.loads(_EVIDENCE.read_text());assert d['status']=='PROMOTED' and d['repair_rows']==1
    assert d['control_applicable']==4194 and d['control_correct']==4194 and d['control_wrong']==0
    return {int(r['game_id']):r for r in d['repairs']}

def join_pbp_rebounds(lineups,pbp_game,alpha:int=5):
    joined,audit=base.join_pbp_rebounds(lineups,pbp_game,alpha=alpha)
    gid=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0;rec=_load().get(gid)
    if rec is None:
        audit=dict(audit);audit['v7_named_player_repairs']=0;return joined,audit
    rebounds=pbp_game[pbp_game.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    hit=rebounds[rebounds.PERIOD.eq(int(rec['period'])) & rebounds.STARTTIME.astype(str).eq(str(rec['start_time'])) & rebounds.ENDTIME.astype(str).eq(str(rec['end_time'])) & rebounds.DESCRIPTION.astype(str).eq(str(rec['pbp_description']))]
    if len(hit)!=1:raise ValueError(f'V7 PBP identity mismatch game={gid} hits={len(hit)}')
    pi=int(hit.index[0])
    if pi in joined.index:raise ValueError(f'V7 row unexpectedly already joined game={gid} pbp_index={pi}')
    lu=tuple(int(x) for x in rec['lineup']);assert len(lu)==10 and len(set(lu))==10 and int(rec['resolved_player_id']) in lu
    add=hit.copy();add['NBA_INDEX']=pd.NA;add['LINEUP']=pd.Series([lu],index=add.index,dtype=object)
    for col in ('EVENTMSGTYPE','EVENTMSGACTIONTYPE','PLAYER1_ID','ELAPSED','EVENTNUM'):add['NBA_'+col]=pd.NA
    add['NBA_IS_REAL_REBOUND']=True;add['REBOUND_LINEAGE']='v7_named_player_interval_synthesis'
    joined=pd.concat([joined,add],axis=0).sort_index(kind='stable')
    audit=dict(audit);audit['matched_rebound_bearing_rows']=int(len(joined));audit['unmatched_rebound_bearing_rows']=max(0,int(audit.get('unmatched_rebound_bearing_rows',1))-1);audit['v7_named_player_repairs']=1
    return joined,audit

def classify_rebounds(pbp_game):return base.classify_rebounds(pbp_game)
