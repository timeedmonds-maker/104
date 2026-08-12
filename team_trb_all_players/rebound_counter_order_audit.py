#!/usr/bin/env python3
"""Compare cumulative rebound-counter ordering strategies against matched truth."""
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
def rows_for(p):
    x=p.copy(); x['_SOURCE_INDEX']=range(len(x)); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['START_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.STARTTIME)]
    r['END_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.ENDTIME)]
    return r

def kind_map(rows, sort_cols):
    prev={}; out={}
    ordered=rows.sort_values(sort_cols,kind='stable')
    for idx,row in ordered.iterrows():
        m=COUNTER_RE.search(str(row.DESCRIPTION))
        if not m: continue
        key=norm(m.group(1)); off=int(m.group(2)); de=int(m.group(3)); po,pd=prev.get(key,(0,0)); kind=None
        if off==po+1 and de==pd: kind='OREB'
        elif de==pd+1 and off==po: kind='DREB'
        if off>=po and de>=pd: prev[key]=(off,de)
        out[idx]={'kind':kind,'off':off,'def':de,'prev_off':po,'prev_def':pd}
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--chunk-id',required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]; y=a.year
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    strategies={
      'start_end_source':['START_ELAPSED','END_ELAPSED','_SOURCE_INDEX'],
      'end_start_source':['END_ELAPSED','START_ELAPSED','_SOURCE_INDEX'],
      'end_source':['END_ELAPSED','_SOURCE_INDEX'],
      'start_source':['START_ELAPSED','_SOURCE_INDEX'],
      'period_end_start_source':['PERIOD','END_ELAPSED','START_ELAPSED','_SOURCE_INDEX'],
      'period_start_end_source':['PERIOD','START_ELAPSED','END_ELAPSED','_SOURCE_INDEX'],
      'source_only':['_SOURCE_INDEX']
    }
    totals={k:{'applicable':0,'correct':0,'wrong':0,'unresolved':0} for k in strategies}; mismatches={k:[] for k in strategies}
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,_=rebound.join_pbp_rebounds(lu,pg[gid]); rows=rows_for(pg[gid]); classified=core.classify_rebounds(joined.copy()); maps={k:kind_map(rows,v) for k,v in strategies.items()}
        for idx,j in joined.iterrows():
            if pd.isna(j.NBA_INDEX) or idx not in rows.index: continue
            desc=str(rows.loc[idx].DESCRIPTION)
            if not COUNTER_RE.search(desc): continue
            ni=int(j.NBA_INDEX); actual_real=bool(core._nba_real_rebound(lu.events,ni)); actual='OREB' if bool(classified.loc[idx].IS_OREB) else ('DREB' if actual_real else None)
            if actual not in {'OREB','DREB'}: continue
            for name,km in maps.items():
                rec=km.get(idx); totals[name]['applicable']+=1
                pred=None if rec is None else rec['kind']
                if pred is None: totals[name]['unresolved']+=1
                elif pred==actual: totals[name]['correct']+=1
                else:
                    totals[name]['wrong']+=1
                    if len(mismatches[name])<100:
                        mismatches[name].append({'game_id':gid,'pbp_index':int(idx),'description':desc,'actual':actual,'predicted':pred,'counter':rec,'start_time':str(rows.loc[idx].STARTTIME),'end_time':str(rows.loc[idx].ENDTIME),'start_elapsed':int(rows.loc[idx].START_ELAPSED),'end_elapsed':int(rows.loc[idx].END_ELAPSED),'source_index':int(rows.loc[idx]._SOURCE_INDEX)})
    out={'chunk_id':a.chunk_id,'year':y,'strategies':totals,'mismatches':mismatches}; a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({'chunk_id':a.chunk_id,'year':y,'strategies':totals},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
