#!/usr/bin/env python3
"""Audit whether a named rebounder uniquely selects the correct lineup inside an ambiguous PBP interval."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
from collections import defaultdict
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v6 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
BRACKET_RE=re.compile(r'^\s*\[[A-Za-z]{2,4}\]\s*')
def norm(v):return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def key(v):return norm(BRACKET_RE.sub('', '' if pd.isna(v) else str(v))).split(' rebound',1)[0].strip()
def rmap(events):
    d=defaultdict(set)
    for _,r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        p=int(r.PLAYER1_ID)
        if 0<p<core.PLAYER_MAX:
            k=key(r.DESCRIPTION_NORM)
            if k:d[k].add(p)
    return {k:next(iter(v)) for k,v in d.items() if len(v)==1}
def elapsed_rows(p):
    r=p[p.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy();r['S']=[core.elapsed_seconds(int(q),c) for q,c in zip(r.PERIOD,r.STARTTIME)];r['E']=[core.elapsed_seconds(int(q),c) for q,c in zip(r.PERIOD,r.ENDTIME)];return r
def predict(events,row,pid,exclude=None):
    lo=min(int(row.S),int(row.E));hi=max(int(row.S),int(row.E));ev=events[events.PERIOD.eq(int(row.PERIOD))&events.ELAPSED.ge(lo)&events.ELAPSED.le(hi)]
    if exclude is not None:ev=ev[ev.index!=exclude]
    lus={tuple(int(x) for x in lu) for lu in ev.LINEUP if int(pid) in set(int(x) for x in lu)}
    return next(iter(lus)) if len(lus)==1 else None
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--games',required=True);ap.add_argument('--chunk-id',required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--v3',type=Path,required=True);ap.add_argument('--pbp',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();ids=[int(x) for x in a.games.split(',') if x]
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False));v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False));pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False));ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    ctl={'applicable':0,'correct':0,'wrong':0};wrong=[];cand=[];residual=0
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]);events=lu.events;joined,_=rebound.join_pbp_rebounds(lu,pg[gid]);rows=elapsed_rows(pg[gid]);mp=rmap(events)
        for idx,row in rows.iterrows():
            p=mp.get(key(row.DESCRIPTION));matched=idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX'])
            if matched and p is not None:
                ni=int(joined.loc[idx,'NBA_INDEX']);pred=predict(events,row,p,ni)
                if pred is not None:
                    ctl['applicable']+=1;actual=tuple(int(x) for x in events.loc[ni,'LINEUP'])
                    if pred==actual:ctl['correct']+=1
                    else:ctl['wrong']+=1;wrong.append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'player_id':int(p),'actual':list(actual),'predicted':list(pred)})
            elif idx not in joined.index:
                residual+=1
                if p is not None:
                    pred=predict(events,row,p,None)
                    if pred is not None:cand.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'player_id':int(p),'lineup':list(pred)})
    out={'status':'DIAGNOSTIC_ONLY','chunk_id':a.chunk_id,'year':a.year,'controls':ctl,'wrong_records':wrong,'current_v6_residual_rows':residual,'repair_candidates':cand};a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k not in {'wrong_records','repair_candidates'} }|{'repair_candidates':len(cand)},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
