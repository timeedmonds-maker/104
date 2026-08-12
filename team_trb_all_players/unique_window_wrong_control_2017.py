#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
import pandas as pd
import local_treb_rebuild as core
import production_rebound_v2 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
from unique_window_rebound_diagnostic import game_start_year,rebound_rows,window_candidates,candidate_payload


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--summary',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    s=json.loads(args.summary.read_text()); targets=sorted(int(g['game_id']) for g in s['all_games'] if g.get('status')=='UNKNOWN_OR_REAL' and game_start_year(int(g['game_id']))==2017)
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba[nba.GAME_ID.isin(targets)].groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3[v3.gameId.isin(targets)].groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp[pbp.GAMEID.isin(targets)].groupby('GAMEID',sort=False)}
    wrong=[]; applicable=correct=duplicate_skipped=0
    for gid in targets:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,audit=rebound.join_pbp_rebounds(lu,pg[gid]); rows=rebound_rows(pg[gid]); counts=Counter(int(x) for x in joined.NBA_INDEX.dropna().astype(int)); used=set(counts); nba_ev=lu.events
        for idx,jrow in joined.iterrows():
            actual=int(jrow.NBA_INDEX)
            if counts[actual]!=1: duplicate_skipped+=1; continue
            src=rows.loc[idx]; cands=window_candidates(nba_ev,src,used-{actual})
            if len(cands)!=1: continue
            applicable+=1; forced=int(cands.index[0])
            if forced==actual: correct+=1; continue
            actual_row=nba_ev.loc[actual]
            wrong.append({
                'game_id':gid,'pbp_index':int(idx),'period':int(src.PERIOD),'start_time':str(src.STARTTIME),'end_time':str(src.ENDTIME),'pbp_description':str(src.DESCRIPTION),
                'actual_nba_index':actual,'actual_eventnum':int(actual_row.EVENTNUM),'actual_elapsed':int(actual_row.ELAPSED),'actual_description':str(actual_row.DESCRIPTION_NORM),'actual_lineup':[int(x) for x in actual_row.LINEUP],
                'forced_nba_index':forced,'forced':candidate_payload(nba_ev,cands,str(src.DESCRIPTION))[0],
                'all_window_rebounds':candidate_payload(nba_ev,nba_ev[nba_ev.PERIOD.eq(src.PERIOD)&nba_ev.EVENTMSGTYPE.eq(4)&nba_ev.ELAPSED.gt(int(src.START_ELAPSED)-5)&nba_ev.ELAPSED.lt(int(src.END_ELAPSED)+5)],str(src.DESCRIPTION)),
                'join_audit':audit,
            })
    out={'year':2017,'targets':len(targets),'control_applicable':applicable,'control_correct':correct,'control_wrong':len(wrong),'duplicate_skipped':duplicate_skipped,'wrong':wrong}
    args.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
