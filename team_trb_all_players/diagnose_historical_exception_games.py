#!/usr/bin/env python3
"""Re-test first-sweep exception games against the current recovery engine.

For failures, preserve compact legacy/v3 evidence around the implicated period
or substitution event. This avoids re-running whole seasons merely to determine
whether a newly added generic repair already resolves a known game.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import production_treb_engine_recovered as engine


def clean(v):
    if pd.isna(v): return None
    if hasattr(v,'item'):
        try: return v.item()
        except Exception: pass
    return v


def rows(df: pd.DataFrame, cols: list[str], n: int = 250):
    cols=[c for c in cols if c in df.columns]
    head=df[cols].head(n)
    return [{k:clean(v) for k,v in r.items()} for r in head.to_dict('records')]


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--control',type=Path,required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    control=json.loads(a.control.read_text()); cases=control[str(a.year)]
    nba=pd.read_csv(a.nba,low_memory=False); nba['GAME_ID']=pd.to_numeric(nba.GAME_ID,errors='coerce')
    v3=pd.read_csv(a.v3,low_memory=False) if a.v3 and a.v3.exists() else pd.DataFrame()
    if len(v3) and 'gameId' in v3: v3['gameId']=pd.to_numeric(v3.gameId,errors='coerce')
    ncols=['GAME_ID','EVENTNUM','EVENTMSGTYPE','EVENTMSGACTIONTYPE','PERIOD','PCTIMESTRING','PERSON1TYPE','PLAYER1_ID','PLAYER1_NAME','PLAYER1_TEAM_ID','PERSON2TYPE','PLAYER2_ID','PLAYER2_NAME','PLAYER2_TEAM_ID','PERSON3TYPE','PLAYER3_ID','PLAYER3_NAME','PLAYER3_TEAM_ID','HOMEDESCRIPTION','NEUTRALDESCRIPTION','VISITORDESCRIPTION']
    vcols=['gameId','actionNumber','orderNumber','period','clock','actionId','actionType','subType','descriptor','qualifiers','description','personId','playerName','teamId','teamTricode','personIdsFilter']
    result=[]
    for case in cases:
        gid=int(case['game_id']); ng=nba[nba.GAME_ID.eq(gid)].copy(); entry={'game_id':gid,'first_sweep_error':case['error'],'nba_rows':int(len(ng))}
        if ng.empty:
            entry['current_status']='NBA_SOURCE_MISSING'
            if len(v3): entry['v3_rows']=int(v3[v3.gameId.eq(gid)].shape[0])
            result.append(entry); continue
        try:
            rebuilt=engine.reconstruct_game_lineups(ng)
            entry.update({'current_status':'PASS','players_with_seconds':len(rebuilt.seconds),'repairs':rebuilt.repairs})
            result.append(entry); continue
        except Exception as exc:
            error=str(exc); entry['current_status']='FAIL'; entry['current_error']=error
        period=None; event=None
        m=re.search(r'period=(\d+)',entry['current_error']); period=int(m.group(1)) if m else None
        m=re.search(r'event=(\d+)',entry['current_error']); event=int(m.group(1)) if m else None
        if event is not None and period is None:
            hit=ng[pd.to_numeric(ng.EVENTNUM,errors='coerce').eq(event)]
            if len(hit): period=int(hit.iloc[0].PERIOD)
        if period is not None:
            npd=ng[pd.to_numeric(ng.PERIOD,errors='coerce').eq(period)].copy()
            entry['legacy_period_rows']=rows(npd,ncols)
            if event is not None:
                ev=pd.to_numeric(npd.EVENTNUM,errors='coerce'); entry['legacy_event_window']=rows(npd[ev.between(event-8,event+8)],ncols)
            entry['legacy_substitutions']=rows(npd[pd.to_numeric(npd.EVENTMSGTYPE,errors='coerce').eq(8)],ncols)
            if len(v3):
                vg=v3[v3.gameId.eq(gid)].copy(); vp=pd.to_numeric(vg.get('period'),errors='coerce') if 'period' in vg else pd.Series(index=vg.index,dtype=float); vpd=vg[vp.eq(period)].copy(); entry['v3_period_rows']=rows(vpd,vcols)
                if event is not None and 'actionNumber' in vpd:
                    va=pd.to_numeric(vpd.actionNumber,errors='coerce'); entry['v3_event_window']=rows(vpd[va.between(event-8,event+8)],vcols)
        result.append(entry)
    payload={'year':a.year,'cases':result,'passes':sum(x['current_status']=='PASS' for x in result),'failures':sum(x['current_status']=='FAIL' for x in result),'source_missing':sum('SOURCE_MISSING' in x['current_status'] for x in result)}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n'); print(json.dumps({k:v for k,v in payload.items() if k!='cases'},indent=2));
    for x in result: print(x['game_id'],x['current_status'],x.get('current_error',''))

if __name__=='__main__': main()
