#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
import production_rebound_v2 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
from invariant_lineup_rebound_diagnostic import game_start_year,rebound_rows,infer_interval_lineup


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--summary',type=Path,required=True); ap.add_argument('--year',type=int,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    s=json.loads(args.summary.read_text()); targets=sorted(int(g['game_id']) for g in s['all_games'] if g.get('status')=='UNKNOWN_OR_REAL' and game_start_year(int(g['game_id']))==args.year)
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba[nba.GAME_ID.isin(targets)].groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3[v3.gameId.isin(targets)].groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp[pbp.GAMEID.isin(targets)].groupby('GAMEID',sort=False)}
    misses=[]; applicable=correct=0
    for gid in targets:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,audit=rebound.join_pbp_rebounds(lu,pg[gid]); rows=rebound_rows(pg[gid])
        for idx,jrow in joined.iterrows():
            src=rows.loc[idx]; inf=infer_interval_lineup(lu.events,src)
            if not inf['invariant']: continue
            applicable+=1; actual=tuple(int(x) for x in jrow.LINEUP); inferred=tuple(int(x) for x in inf['lineup'])
            if inferred==actual: correct+=1; continue
            lo=min(int(src.START_ELAPSED),int(src.END_ELAPSED)); hi=max(int(src.START_ELAPSED),int(src.END_ELAPSED))
            neighborhood=lu.events[lu.events.PERIOD.eq(src.PERIOD)&lu.events.ELAPSED.ge(lo-3)&lu.events.ELAPSED.le(hi+3)].copy()
            events=[]
            for _,r in neighborhood.sort_values(['ELAPSED','EVENTNUM'],kind='stable').iterrows():
                events.append({'eventnum':int(r.EVENTNUM),'elapsed':int(r.ELAPSED),'type':int(r.EVENTMSGTYPE),'description':str(r.DESCRIPTION_NORM),'lineup':[int(x) for x in r.LINEUP]})
            misses.append({'game_id':gid,'pbp_index':int(idx),'period':int(src.PERIOD),'start_time':str(src.STARTTIME),'end_time':str(src.ENDTIME),'start_elapsed':int(src.START_ELAPSED),'end_elapsed':int(src.END_ELAPSED),'description':str(src.DESCRIPTION),'actual_nba_eventnum':int(jrow.NBA_EVENTNUM),'actual_nba_elapsed':int(jrow.NBA_ELAPSED),'actual_lineup':list(actual),'inferred_lineup':list(inferred),'interval':inf,'neighborhood':events,'join_audit':audit})
    out={'year':args.year,'targets':len(targets),'control_applicable':applicable,'control_correct':correct,'control_wrong':len(misses),'misses':misses}; args.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
