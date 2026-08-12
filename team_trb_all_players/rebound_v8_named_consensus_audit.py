#!/usr/bin/env python3
"""V8 diagnostic: test narrow named-player lineup rules on V7 controls/residuals."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rebound_v5_source_only_audit as a
import production_rebound_v7 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
import local_treb_rebuild as core

def candidates(events,row,rmap,exclude=None):
    pid=rmap.get(a.name_key(row.DESCRIPTION)); lp=a.lineup_predictions(events,row,exclude)
    if pid is None:return {}
    def ok(lu):return lu is not None and int(pid) in set(map(int,lu))
    out={}
    if ok(lp['prior_miss_exact']):out['named_prior_miss']=lp['prior_miss_exact']
    if ok(lp['endpoint_gap0']):out['named_endpoint_gap0']=lp['endpoint_gap0']
    if ok(lp['clock_invariant']):out['named_clock_invariant']=lp['clock_invariant']
    if ok(lp['interval_invariant']):out['named_interval_invariant']=lp['interval_invariant']
    if ok(lp['endpoint_gap0']) and lp['clock_invariant']==lp['endpoint_gap0']:out['named_endpoint_clock']=lp['endpoint_gap0']
    vals=[v for k,v in lp.items() if k in ('prior_miss_exact','endpoint_gap0','clock_invariant','interval_invariant') and ok(v)]
    if vals and all(v==vals[0] for v in vals):out['named_available_consensus']=vals[0]
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--games',required=True);ap.add_argument('--chunk-id',required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--v3',type=Path,required=True);ap.add_argument('--pbp',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);z=ap.parse_args()
    ids=[int(x) for x in z.games.split(',') if x]
    nba=io.normalize_nba(pd.read_csv(z.nba,low_memory=False));v3=lineup_engine.normalize_v3(pd.read_csv(z.v3,low_memory=False));pbp=io.normalize_pbp(pd.read_csv(z.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    names=['named_prior_miss','named_endpoint_gap0','named_clock_invariant','named_interval_invariant','named_endpoint_clock','named_available_consensus']
    ctl={n:{'applicable':0,'correct':0,'wrong':0} for n in names};wrong={n:[] for n in names};res=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]);ev=lu.events;joined,_=rebound.join_pbp_rebounds(lu,pg[gid]);rows=a.rows_for_game(pg[gid]);rmap=a.rebounder_map(ev)
        for idx,row in rows.iterrows():
            matched=idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX'])
            if matched:
                ni=int(joined.loc[idx,'NBA_INDEX']);actual=tuple(int(x) for x in ev.loc[ni,'LINEUP']);real=bool(core._nba_real_rebound(ev,ni))
                for n,pred in candidates(ev,row,rmap,ni).items():
                    ctl[n]['applicable']+=1; good=(pred==actual and real)
                    ctl[n]['correct' if good else 'wrong']+=1
                    if not good and len(wrong[n])<20:wrong[n].append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'actual_real':real,'actual':list(actual),'predicted':list(pred)})
            elif idx not in joined.index:
                cs=candidates(ev,row,rmap,None)
                if cs:res.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'resolved_player_id':rmap.get(a.name_key(row.DESCRIPTION)),'strategies':{k:list(v) for k,v in cs.items()}})
    out={'status':'DIAGNOSTIC_ONLY','chunk_id':z.chunk_id,'year':z.year,'controls':ctl,'wrong_records':wrong,'residual_candidates':res};z.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'chunk_id':z.chunk_id,'controls':ctl,'residual_candidates':len(res)},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
