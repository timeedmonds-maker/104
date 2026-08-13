#!/usr/bin/env python3
"""V9 diagnostic: test non-bracket named counter-DREB endpoint/clock anchors against production V8."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rebound_v5_source_only_audit as a
import production_rebound_v8 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
import local_treb_rebuild as core

BRACKET_RE=re.compile(r'^\s*\[[A-Za-z]{2,4}\]')

def rules(events,row,rmap,exclude=None):
    desc=str(row.DESCRIPTION)
    pid=rmap.get(a.name_key(desc))
    if pid is None or bool(row.PBP_IS_OREB) or not a.COUNTER_RE.search(desc) or BRACKET_RE.match(desc):
        return {}
    lp=a.lineup_predictions(events,row,exclude)
    def valid(lu):
        return lu is not None and int(pid) in set(map(int,lu))
    out={}
    ep=lp['endpoint_gap0']; clk=lp['clock_invariant']
    if valid(ep): out['nonbracket_dreb_counter_endpoint_named']=ep
    if valid(clk): out['nonbracket_dreb_counter_clock_named']=clk
    if valid(ep) and clk==ep: out['nonbracket_dreb_counter_endpoint_clock_named']=ep
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--year',type=int,required=True);p.add_argument('--games',required=True);p.add_argument('--chunk-id',required=True);p.add_argument('--nba',type=Path,required=True);p.add_argument('--v3',type=Path,required=True);p.add_argument('--pbp',type=Path,required=True);p.add_argument('--output',type=Path,required=True);z=p.parse_args()
    ids=[int(x) for x in z.games.split(',') if x]
    nba=io.normalize_nba(pd.read_csv(z.nba,low_memory=False));v3=lineup_engine.normalize_v3(pd.read_csv(z.v3,low_memory=False));pbp=io.normalize_pbp(pd.read_csv(z.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    names=['nonbracket_dreb_counter_endpoint_named','nonbracket_dreb_counter_clock_named','nonbracket_dreb_counter_endpoint_clock_named']
    ctl={n:{'applicable':0,'correct':0,'wrong':0} for n in names};wrong={n:[] for n in names};res=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]);ev=lu.events;joined,_=rebound.join_pbp_rebounds(lu,pg[gid]);rows=a.rows_for_game(pg[gid]);rmap=a.rebounder_map(ev)
        for idx,row in rows.iterrows():
            matched=idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX'])
            rr=rules(ev,row,rmap,int(joined.loc[idx,'NBA_INDEX']) if matched else None)
            if matched:
                ni=int(joined.loc[idx,'NBA_INDEX']);actual=tuple(int(x) for x in ev.loc[ni,'LINEUP']);real=bool(core._nba_real_rebound(ev,ni))
                for n,pred in rr.items():
                    ctl[n]['applicable']+=1;good=(pred==actual and real);ctl[n]['correct' if good else 'wrong']+=1
                    if not good:wrong[n].append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'actual_real':real,'actual':list(actual),'predicted':list(pred)})
            elif idx not in joined.index and rr:
                res.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'resolved_player_id':rmap.get(a.name_key(row.DESCRIPTION)),'strategies':{k:list(v) for k,v in rr.items()}})
    z.output.write_text(json.dumps({'status':'DIAGNOSTIC_ONLY','chunk_id':z.chunk_id,'year':z.year,'controls':ctl,'wrong_records':wrong,'residual_candidates':res},indent=2)+'\n');return 0
if __name__=='__main__':raise SystemExit(main())
