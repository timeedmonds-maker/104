#!/usr/bin/env python3
"""Production rebound layer v5: v4 plus finite direct-team event-identity repairs.

No matcher is widened.  The only additions are the 50 explicitly promoted PBP
team-rebound rows in DIRECT_TEAM_REBOUND_REPAIRS.json.  Every PBP identity,
NBA event identity, team placeholder ID, elapsed time, lineup, real/dead status,
and non-reuse invariant is asserted at runtime.
"""
from __future__ import annotations
import json,re
from pathlib import Path
import pandas as pd
import local_treb_rebuild as core
import production_rebound_v4 as base

_EVIDENCE=Path(__file__).resolve().parent/'final_integrity_rebuild'/'rebound_forensics'/'DIRECT_TEAM_REBOUND_REPAIRS.json'

def _load():
    d=json.loads(_EVIDENCE.read_text());assert d.get('status')=='PROMOTED';assert int(d.get('repair_rows',0))==50
    assert int(d.get('direct_team_control_wrong',-1))==0 and int(d.get('bracket_prefix_control_wrong',-1))==0
    out={}
    for r in d['repairs']:out.setdefault(int(r['game_id']),[]).append(r)
    assert sum(map(len,out.values()))==50
    return out

def _rows(pbp):
    r=pbp[pbp.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['START_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(r.PERIOD,r.STARTTIME)]
    r['END_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(r.PERIOD,r.ENDTIME)]
    return r

def join_pbp_rebounds(lineups:core.GameLineups,pbp_game:pd.DataFrame,alpha:int=5):
    joined,audit=base.join_pbp_rebounds(lineups,pbp_game,alpha=alpha)
    gid=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    repairs=_load().get(gid,[])
    if not repairs:
        audit=dict(audit);audit['direct_team_repairs']=0;return joined,audit
    nba=lineups.events;rows=_rows(pbp_game);used=set(pd.to_numeric(joined.NBA_INDEX,errors='coerce').dropna().astype(int));adds=[];applied=[]
    for rec in repairs:
        period=int(rec['period']);start=str(rec['start_time']);end=str(rec['end_time']);desc=str(rec['pbp_description'])
        assert re.fullmatch(r'\[[A-Z]{2,4}\] Team Rebound',desc)
        hit=rows[rows.PERIOD.eq(period)&rows.STARTTIME.astype(str).eq(start)&rows.ENDTIME.astype(str).eq(end)&rows.DESCRIPTION.astype(str).eq(desc)]
        if len(hit)!=1:raise ValueError(f'direct-team PBP identity mismatch game={gid} desc={desc!r} hits={len(hit)}')
        pi=int(hit.index[0]);
        if pi in joined.index:raise ValueError(f'direct-team row already matched game={gid} pbp_index={pi}')
        nh=nba[nba.PERIOD.eq(period)&nba.EVENTNUM.eq(int(rec['nba_eventnum']))]
        if len(nh)!=1:raise ValueError(f'direct-team NBA identity mismatch game={gid} event={rec["nba_eventnum"]} hits={len(nh)}')
        ni=int(nh.index[0])
        if ni in used:raise ValueError(f'direct-team NBA event reuse game={gid} event={rec["nba_eventnum"]}')
        if int(nba.loc[ni,'EVENTMSGTYPE'])!=4:raise ValueError('direct-team target not rebound')
        if int(nba.loc[ni,'PLAYER1_ID'])!=int(rec['resolved_team_id']) or int(nba.loc[ni,'PLAYER1_ID'])!=int(rec['nba_player1_id']):raise ValueError('direct-team placeholder/team drift')
        if int(nba.loc[ni,'ELAPSED'])!=int(rec['nba_elapsed']):raise ValueError('direct-team elapsed drift')
        lineup=[int(x) for x in nba.loc[ni,'LINEUP']]
        if lineup!=[int(x) for x in rec['lineup']]:raise ValueError('direct-team lineup drift')
        real=bool(core._nba_real_rebound(nba,ni))
        if real!=bool(rec['real']):raise ValueError('direct-team real/dead drift')
        add=hit.copy();add['NBA_INDEX']=ni;add['LINEUP']=pd.Series([nba.loc[ni,'LINEUP']],index=add.index,dtype=object)
        for col in ('EVENTMSGTYPE','EVENTMSGACTIONTYPE','PLAYER1_ID','ELAPSED','EVENTNUM'):add['NBA_'+col]=nba.loc[ni,col]
        add['NBA_IS_REAL_REBOUND']=real;adds.append(add);used.add(ni)
        applied.append({'pbp_index':pi,'period':period,'start_time':start,'end_time':end,'pbp_description':desc,'nba_eventnum':int(rec['nba_eventnum']),'real':real,'method':'finite_direct_team_event_identity'})
    if adds:joined=pd.concat([joined,*adds],axis=0).sort_index(kind='stable')
    matched=set(joined.index);remaining=[]
    for idx,row in rows.iterrows():
        if idx not in matched:remaining.append({'game_id':gid,'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION)})
    audit=dict(audit);audit['matched_rebound_bearing_rows']=int(len(joined));audit['unmatched_rebound_bearing_rows']=len(remaining);audit['unmatched_rows']=remaining;audit['direct_team_repairs']=len(applied);audit['direct_team_records']=applied
    return joined,audit

def classify_rebounds(pbp_game:pd.DataFrame):return base.classify_rebounds(pbp_game)
