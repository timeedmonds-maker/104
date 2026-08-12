#!/usr/bin/env python3
"""Validate counter-proven DREB lineup anchors on an explicit game list."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v4 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

COUNTER_RE=re.compile(r'^(.*?)\s+REBOUND\s+\(Off:(\d+) Def:(\d+)\)',re.I)
def norm(v): return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def make_rows(p):
    x=p.copy(); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['END_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.ENDTIME)]
    return r
def rebound_kind_map(rows):
    prev={}; out={}
    for idx,row in rows.sort_index().iterrows():
        m=COUNTER_RE.search(str(row.DESCRIPTION))
        if not m: continue
        key=norm(m.group(1)); off=int(m.group(2)); de=int(m.group(3)); po,pd=prev.get(key,(0,0)); kind=None
        if off==po+1 and de==pd: kind='OREB'
        elif de==pd+1 and off==po: kind='DREB'
        if off>=po and de>=pd: prev[key]=(off,de)
        out[idx]={'player_key':key,'off':off,'def':de,'prev_off':po,'prev_def':pd,'kind':kind}
    return out
def endpoint(events,period,t,exclude=None,radius=5):
    ev=events[events.PERIOD.eq(period)].sort_values(['ELAPSED','EVENTNUM'],kind='stable')
    if exclude is not None: ev=ev[ev.index!=exclude]
    if bool(ev[ev.ELAPSED.ge(t-radius)&ev.ELAPSED.le(t+radius)].EVENTMSGTYPE.eq(8).any()): return None
    bef=ev[ev.ELAPSED.le(t)]; aft=ev[ev.ELAPSED.ge(t)]
    if bef.empty or aft.empty:return None
    a=tuple(int(x) for x in bef.iloc[-1].LINEUP); b=tuple(int(x) for x in aft.iloc[0].LINEUP)
    return a if a==b else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--chunk-id',required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]; y=a.year
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    controls={k:0 for k in ['kind_applicable','kind_correct','kind_wrong','endpoint_applicable','endpoint_correct','endpoint_wrong','miss_applicable','miss_correct','miss_wrong','both_applicable','both_correct','both_wrong']}; residual=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,audit=rebound.join_pbp_rebounds(lu,pg[gid]); events=lu.events; rows=make_rows(pg[gid]); kinds=rebound_kind_map(rows); classified=core.classify_rebounds(joined.copy())
        for idx,row in rows.iterrows():
            k=kinds.get(idx); is_dreb=bool(k and k['kind']=='DREB'); prev_desc=norm(row.PREV_PBP_DESCRIPTION)
            mh=events[events.PERIOD.eq(row.PERIOD)&events.DESCRIPTION_NORM.eq(prev_desc)&events.EVENTMSGTYPE.isin([2,3])]; miss=None; miss_source=None
            if len(mh)==1:
                mi=int(mh.index[0]); miss=tuple(int(x) for x in events.loc[mi,'LINEUP']); miss_source={'eventnum':int(events.loc[mi,'EVENTNUM']),'elapsed':int(events.loc[mi,'ELAPSED']),'description':str(events.loc[mi,'DESCRIPTION_NORM']),'eventmsgtype':int(events.loc[mi,'EVENTMSGTYPE'])}
            if idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX']):
                ni=int(joined.loc[idx,'NBA_INDEX']); actual=tuple(int(x) for x in events.loc[ni,'LINEUP']); actual_real=bool(core._nba_real_rebound(events,ni))
                if k and k['kind'] in {'DREB','OREB'}:
                    controls['kind_applicable']+=1; actual_kind='OREB' if bool(classified.loc[idx].IS_OREB) else ('DREB' if actual_real else None)
                    if actual_kind==k['kind']:controls['kind_correct']+=1
                    else:controls['kind_wrong']+=1
                if is_dreb:
                    ep=endpoint(events,int(row.PERIOD),int(row.END_ELAPSED),exclude=ni)
                    if ep is not None:
                        controls['endpoint_applicable']+=1
                        if ep==actual:controls['endpoint_correct']+=1
                        else:controls['endpoint_wrong']+=1
                    if miss is not None:
                        controls['miss_applicable']+=1
                        if miss==actual:controls['miss_correct']+=1
                        else:controls['miss_wrong']+=1
                    if ep is not None and miss is not None and ep==miss:
                        controls['both_applicable']+=1
                        if ep==actual:controls['both_correct']+=1
                        else:controls['both_wrong']+=1
            elif idx not in joined.index:
                ep=endpoint(events,int(row.PERIOD),int(row.END_ELAPSED),exclude=None) if is_dreb else None
                residual.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'previous_description':'' if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),'counter_evidence':k,'is_counter_dreb':is_dreb,'endpoint_lineup':list(ep) if ep is not None else None,'prior_miss_lineup':list(miss) if miss is not None else None,'anchors_agree':bool(ep is not None and miss is not None and ep==miss),'prior_miss_source':miss_source})
    out={'chunk_id':a.chunk_id,'year':y,'controls':controls,'residual_rows':residual}; a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({'chunk_id':a.chunk_id,'year':y,'controls':controls,'residual_rows':len(residual)},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
