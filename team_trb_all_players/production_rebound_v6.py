#!/usr/bin/env python3
"""Production rebound layer v6: v5 plus seven finite source-only player rows.

These seven PBP Stats rebound rows have no usable NBA rebound event.  They are
therefore represented honestly as synthetic attribution rows: exact PBP identity,
control-proven invariant lineup, and control-proven live=True.  No fake NBA event
identity is created.  OREB/DREB remains determined later by the locked PBP Stats
possession classification.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import production_rebound_v5 as base

_EVIDENCE=Path(__file__).resolve().parent/'final_integrity_rebuild'/'rebound_forensics'/'V6_SOURCE_ONLY_PLAYER_REPAIRS.json'

def _load():
    d=json.loads(_EVIDENCE.read_text());assert d.get('status')=='PROMOTED' and int(d.get('repair_rows',0))==7
    assert int(d.get('lineup_control_wrong',-1))==0 and int(d.get('named_player_live_control_wrong',-1))==0
    by={}
    for r in d['repairs']:by.setdefault(int(r['game_id']),[]).append(r)
    assert sum(map(len,by.values()))==7;return by

def join_pbp_rebounds(lineups,pbp_game,alpha:int=5):
    joined,audit=base.join_pbp_rebounds(lineups,pbp_game,alpha=alpha)
    gid=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0;repairs=_load().get(gid,[])
    if not repairs:
        audit=dict(audit);audit['source_only_player_repairs']=0;return joined,audit
    rebounds=pbp_game[pbp_game.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy();adds=[];records=[]
    for rec in repairs:
        hit=rebounds[
          rebounds.PERIOD.eq(int(rec['period'])) & rebounds.STARTTIME.astype(str).eq(str(rec['start_time'])) &
          rebounds.ENDTIME.astype(str).eq(str(rec['end_time'])) & rebounds.DESCRIPTION.astype(str).eq(str(rec['pbp_description']))]
        if len(hit)!=1:raise ValueError(f'V6 source-only PBP identity mismatch game={gid} desc={rec["pbp_description"]!r} hits={len(hit)}')
        pi=int(hit.index[0])
        if pi in joined.index:raise ValueError(f'V6 source-only row unexpectedly already joined game={gid} pbp_index={pi}')
        lu=tuple(int(x) for x in rec['lineup']);assert len(lu)==10 and len(set(lu))==10
        if int(rec['resolved_player_id']) not in lu:raise ValueError(f'V6 rebounder absent from promoted lineup game={gid} player={rec["resolved_player_id"]}')
        add=hit.copy();add['NBA_INDEX']=pd.NA;add['LINEUP']=pd.Series([lu],index=add.index,dtype=object)
        for col in ('EVENTMSGTYPE','EVENTMSGACTIONTYPE','PLAYER1_ID','ELAPSED','EVENTNUM'):add['NBA_'+col]=pd.NA
        add['NBA_IS_REAL_REBOUND']=True;add['REBOUND_LINEAGE']='finite_source_only_player_synthesis'
        adds.append(add);records.append({'pbp_index':pi,'description':str(rec['pbp_description']),'resolved_player_id':int(rec['resolved_player_id']),'method':str(rec['resolution_method'])})
    if adds:joined=pd.concat([joined,*adds],axis=0).sort_index(kind='stable')
    matched=set(joined.index);remaining=[]
    for idx,row in rebounds.iterrows():
        if idx not in matched:remaining.append({'game_id':gid,'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION)})
    audit=dict(audit);audit['matched_rebound_bearing_rows']=int(len(joined));audit['unmatched_rebound_bearing_rows']=len(remaining);audit['unmatched_rows']=remaining;audit['source_only_player_repairs']=len(records);audit['source_only_player_records']=records
    return joined,audit

def classify_rebounds(pbp_game):return base.classify_rebounds(pbp_game)
