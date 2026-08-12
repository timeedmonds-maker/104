#!/usr/bin/env python3
"""Validate metric-equivalent rebound repair evidence, not player identity.

A row is eligible only when:
- its rebounder name key resolves to one game-local NBA rebounder ID;
- the exact prior missed shot resolves inside the PBP possession window;
- endpoint-bracket and missed-shot lineups agree exactly;
- both resolved rebounder and shooter are members of that agreed lineup.
For TREB, the required outputs are then only (lineup, real=True, OREB/DREB),
where OREB iff rebounder team == shooter team. Matched controls compare those
outputs directly to NBA rebound truth; rebounder identity need not itself match.
"""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v4 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

def norm(v): return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def name_key(desc): return norm(desc).split(' rebound',1)[0].strip()
def make_rows(p):
    x=p.copy(); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['START_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.STARTTIME)]
    r['END_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.ENDTIME)]
    return r
def rebounder_map(events):
    d={}
    for _,r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid=int(r.PLAYER1_ID); k=name_key(r.DESCRIPTION_NORM)
        if 0<pid<core.PLAYER_MAX and k:d.setdefault(k,set()).add(pid)
    return {k:next(iter(v)) for k,v in d.items() if len(v)==1}
def prior_miss(events,row):
    prev=norm(row.PREV_PBP_DESCRIPTION)
    if not prev:return None
    h=events[events.PERIOD.eq(row.PERIOD)&events.DESCRIPTION_NORM.eq(prev)&events.EVENTMSGTYPE.isin([2,3])]
    h=h[h.ELAPSED.ge(int(row.START_ELAPSED)-5)&h.ELAPSED.le(int(row.END_ELAPSED)+5)]
    if len(h)!=1:return None
    i=int(h.index[0]); pid=int(events.loc[i,'PLAYER1_ID'])
    return {'nba_index':i,'eventnum':int(events.loc[i,'EVENTNUM']),'elapsed':int(events.loc[i,'ELAPSED']),'description':str(events.loc[i,'DESCRIPTION_NORM']),'shooter_id':pid,'lineup':tuple(int(x) for x in events.loc[i,'LINEUP'])}
def endpoint(events,row,exclude=None,radius=5):
    t=int(row.END_ELAPSED); ev=events[events.PERIOD.eq(int(row.PERIOD))].sort_values(['ELAPSED','EVENTNUM'],kind='stable')
    if exclude is not None:ev=ev[ev.index!=exclude]
    if bool(ev[ev.ELAPSED.ge(t-radius)&ev.ELAPSED.le(t+radius)].EVENTMSGTYPE.eq(8).any()):return None
    bef=ev[ev.ELAPSED.le(t)]; aft=ev[ev.ELAPSED.ge(t)]
    if bef.empty or aft.empty:return None
    a=tuple(int(x) for x in bef.iloc[-1].LINEUP); b=tuple(int(x) for x in aft.iloc[0].LINEUP)
    return a if a==b else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--chunk-id',required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]; y=a.year
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    c={k:0 for k in ['applicable','lineup_correct','lineup_wrong','kind_correct','kind_wrong','real_correct','real_wrong','impact_correct','impact_wrong']}; residual=[]; wrong=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,_=rebound.join_pbp_rebounds(lu,pg[gid]); events=lu.events; rows=make_rows(pg[gid]); rmap=rebounder_map(events); pteam=core._player_team(ng[gid])
        for idx,row in rows.iterrows():
            rid=rmap.get(name_key(row.DESCRIPTION)); miss=prior_miss(events,row)
            if idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX']):
                ni=int(joined.loc[idx,'NBA_INDEX']); ep=endpoint(events,row,exclude=ni)
                if rid is None or miss is None or ep is None or ep!=miss['lineup']:continue
                if rid not in ep or int(miss['shooter_id']) not in ep:continue
                rtid=pteam.get(rid); stid=pteam.get(int(miss['shooter_id']))
                if rtid is None or stid is None:continue
                pred_oreb=int(rtid)==int(stid); pred_real=True; actual_lineup=tuple(int(x) for x in events.loc[ni,'LINEUP']); actual_real=bool(core._nba_real_rebound(events,ni))
                actual_rid=int(events.loc[ni,'PLAYER1_ID']); artid=pteam.get(actual_rid)
                if artid is None:continue
                actual_oreb=int(artid)==int(stid)
                c['applicable']+=1
                if ep==actual_lineup:c['lineup_correct']+=1
                else:c['lineup_wrong']+=1
                if pred_oreb==actual_oreb:c['kind_correct']+=1
                else:c['kind_wrong']+=1
                if pred_real==actual_real:c['real_correct']+=1
                else:c['real_wrong']+=1
                good=ep==actual_lineup and pred_oreb==actual_oreb and actual_real
                if good:c['impact_correct']+=1
                else:
                    c['impact_wrong']+=1
                    wrong.append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'resolved_rebounder_id':rid,'actual_rebounder_id':actual_rid,'predicted_oreb':pred_oreb,'actual_oreb':actual_oreb,'predicted_lineup':list(ep),'actual_lineup':list(actual_lineup),'prior_miss_eventnum':miss['eventnum']})
            elif idx not in joined.index:
                ep=endpoint(events,row,exclude=None)
                rtid=pteam.get(rid) if rid is not None else None; stid=pteam.get(int(miss['shooter_id'])) if miss is not None else None
                eligible=bool(rid is not None and miss is not None and ep is not None and ep==miss['lineup'] and rid in ep and int(miss['shooter_id']) in ep and rtid is not None and stid is not None)
                residual.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'previous_description':'' if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),'eligible':eligible,'resolved_rebounder_id':rid,'rebounder_team_id':int(rtid) if rtid is not None else None,'shooter_id':int(miss['shooter_id']) if miss is not None else None,'shooter_team_id':int(stid) if stid is not None else None,'prior_miss_eventnum':int(miss['eventnum']) if miss is not None else None,'prior_miss_elapsed':int(miss['elapsed']) if miss is not None else None,'prior_miss_description':str(miss['description']) if miss is not None else None,'lineup':list(ep) if eligible else None,'real':True if eligible else None,'is_oreb':bool(int(rtid)==int(stid)) if eligible else None})
    out={'chunk_id':a.chunk_id,'year':y,'controls':c,'control_wrong_records':wrong,'residual_rows':residual}; a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({'chunk_id':a.chunk_id,'year':y,'controls':c,'residual_rows':len(residual),'eligible_residual_rows':sum(r['eligible'] for r in residual)},indent=2)); return 0
if __name__=='__main__':raise SystemExit(main())
