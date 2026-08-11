#!/usr/bin/env python3
"""Map each historical dead team-rebound reason onto modern 2024 context fields."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core

PLAYER_MAX = core.PLAYER_MAX


def clean(v):
    if pd.isna(v): return None
    if hasattr(v,'item'):
        try: return v.item()
        except Exception: pass
    return v


def legacy_reason(nba: pd.DataFrame, position: int) -> str:
    loc=nba.index.get_loc(position); row=nba.loc[position]
    player=int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
    team=player==0 or player>=PLAYER_MAX
    action=int(row.EVENTMSGACTIONTYPE)
    if team and action!=0: return 'nonzero_rebound_action'
    previous=nba.iloc[loc-1] if loc else None
    prior_shot=previous; scan=loc-1
    while scan>=0 and int(nba.iloc[scan].ELAPSED)==int(row.ELAPSED):
        cand=nba.iloc[scan]
        if int(cand.EVENTMSGTYPE) in (1,2,3,5):
            prior_shot=cand; break
        scan-=1
    same=nba[(nba.PERIOD.eq(row.PERIOD)) & nba.ELAPSED.eq(row.ELAPSED)]
    if team and ((same.EVENTMSGTYPE.eq(5)) & same.EVENTMSGACTIONTYPE.isin([11,19])).any():
        return 'same_clock_turnover_placeholder'
    if prior_shot is not None and int(prior_shot.EVENTMSGTYPE)==3 and 'miss ' in prior_shot.DESCRIPTION_NORM:
        end_actions={10,12,15,30,31,32,35,36,37}
        if int(prior_shot.EVENTMSGACTIONTYPE) not in end_actions or ' flagrant' in prior_shot.DESCRIPTION_NORM:
            return 'nonfinal_or_flagrant_missed_ft'
    nxt=loc+1
    while nxt<len(nba) and int(nba.iloc[nxt].EVENTMSGTYPE)==18: nxt+=1
    next_is_end=nxt>=len(nba) or int(nba.iloc[nxt].EVENTMSGTYPE)==13
    period_end=int(row.PERIOD)*720 if int(row.PERIOD)<=4 else 2880+(int(row.PERIOD)-4)*300
    if team and int(row.ELAPSED)==period_end and next_is_end:
        return 'horn_exact_end'
    if team and previous is not None and int(row.ELAPSED)==int(previous.ELAPSED) and next_is_end:
        if period_end-int(row.ELAPSED)<=3:
            return 'horn_within_3_seconds'
    return 'real'


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    nba=pd.read_csv(a.nba,low_memory=False); v3=pd.read_csv(a.v3,low_memory=False)
    nba['GAME_ID']=pd.to_numeric(nba.GAME_ID,errors='raise').astype('int64'); nba['EVENTNUM']=pd.to_numeric(nba.EVENTNUM,errors='raise').astype('int64')
    v3['gameId']=pd.to_numeric(v3.gameId,errors='raise').astype('int64'); v3['actionNumber']=pd.to_numeric(v3.actionNumber,errors='raise').astype('int64')
    if 'orderNumber' not in v3: v3['orderNumber']=v3.actionNumber
    v3['orderNumber']=pd.to_numeric(v3.orderNumber,errors='coerce').fillna(v3.actionNumber).astype('int64')
    records=[]
    for gid,ng0 in nba.groupby('GAME_ID',sort=False):
        vg=v3[v3.gameId.eq(int(gid))].sort_values(['period','orderNumber','actionNumber'],kind='stable').reset_index(drop=True)
        if vg.empty: continue
        by_action={int(r.actionNumber):i for i,r in vg.iterrows()}
        ng=ng0.sort_values(['PERIOD','EVENTNUM'],kind='stable').copy().reset_index(drop=True)
        ng['DESCRIPTION_NORM']=core.nba_description(ng); ng['ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(ng.PERIOD,ng.PCTIMESTRING)]
        for idx,row in ng[ng.EVENTMSGTYPE.eq(4)].iterrows():
            pid=int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
            if 0<pid<PLAYER_MAX: continue
            reason=legacy_reason(ng,idx)
            if reason=='real': continue
            ai=by_action.get(int(row.EVENTNUM))
            if ai is None: continue
            mr=vg.iloc[ai]
            def shape(rr):
                if rr is None: return None
                return {c:clean(rr.get(c)) for c in ['actionNumber','orderNumber','period','clock','actionId','actionType','subType','descriptor','qualifiers','description','shotResult','personId','teamId'] if c in vg.columns}
            same_clock=[]
            for j,r2 in vg.iterrows():
                if int(r2.period)==int(mr.period) and str(r2.clock)==str(mr.clock) and int(r2.actionNumber)!=int(mr.actionNumber): same_clock.append(shape(r2))
            prev=shape(vg.iloc[ai-1]) if ai>0 and int(vg.iloc[ai-1].period)==int(mr.period) else None
            nxt=shape(vg.iloc[ai+1]) if ai+1<len(vg) and int(vg.iloc[ai+1].period)==int(mr.period) else None
            records.append({'game_id':int(gid),'action_number':int(row.EVENTNUM),'legacy_reason':reason,'legacy_action_type':int(row.EVENTMSGACTIONTYPE),'legacy_description':str(row.DESCRIPTION_NORM),'modern':shape(mr),'previous':prev,'next':nxt,'same_clock':same_clock})
    counts=Counter(r['legacy_reason'] for r in records)
    summaries={}
    for reason in counts:
        grp=[r for r in records if r['legacy_reason']==reason]
        def cnt(path):
            c=Counter()
            for r in grp:
                x=r
                for k in path:
                    x=x.get(k) if isinstance(x,dict) and x is not None else None
                c[str(x)]+=1
            return c.most_common(30)
        summaries[reason]={
            'count':len(grp),
            'modern_subType':cnt(['modern','subType']),
            'modern_descriptor':cnt(['modern','descriptor']),
            'previous_actionType':cnt(['previous','actionType']),
            'previous_subType':cnt(['previous','subType']),
            'previous_descriptor':cnt(['previous','descriptor']),
            'previous_shotResult':cnt(['previous','shotResult']),
            'next_actionType':cnt(['next','actionType']),
            'same_clock_action_types':Counter(x.get('actionType') for r in grp for x in r['same_clock']).most_common(30),
        }
    payload={'reason_counts':dict(counts),'summaries':summaries,'records':records}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({'reason_counts':dict(counts),'summaries':summaries},indent=2,default=str))

if __name__=='__main__': main()
