#!/usr/bin/env python3
"""Test invariant-lineup + semantic real-status recovery for unmatched rebounds.

For TREB attribution we need: (a) the ten-player lineup, (b) PBP rebound order / offense
context, and (c) whether a defensive/generic rebound is a real rebound rather than a
placeholder. Exact NBA rebound event identity is not required when these are independently
forced. This diagnostic tests that proposition against matched controls before promotion.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import pandas as pd
import local_treb_rebuild as core
import production_rebound_v2 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
from invariant_lineup_rebound_diagnostic import game_start_year,rebound_rows,infer_interval_lineup,is_player_credited


def rebound_candidates(nba:pd.DataFrame,row:pd.Series,alpha:int=5)->pd.DataFrame:
    return nba[nba.PERIOD.eq(row.PERIOD)&nba.EVENTMSGTYPE.eq(4)&nba.ELAPSED.gt(int(row.START_ELAPSED)-alpha)&nba.ELAPSED.lt(int(row.END_ELAPSED)+alpha)].copy()

def consensus_real(nba:pd.DataFrame,cands:pd.DataFrame):
    if len(cands)==0: return None
    vals={bool(core._nba_real_rebound(nba,int(i))) for i in cands.index}
    return next(iter(vals)) if len(vals)==1 else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--summary',type=Path,required=True); ap.add_argument('--year',type=int,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    s=json.loads(args.summary.read_text()); targets=sorted(int(g['game_id']) for g in s['all_games'] if g.get('status')=='UNKNOWN_OR_REAL' and game_start_year(int(g['game_id']))==args.year)
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba[nba.GAME_ID.isin(targets)].groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3[v3.gameId.isin(targets)].groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp[pbp.GAMEID.isin(targets)].groupby('GAMEID',sort=False)}
    lc=lc_ok=lc_bad=0; rc=rc_ok=rc_bad=0; target_safe_rows=target_safe_games=0; games=[]
    for gid in targets:
        try:
            lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,audit=rebound.join_pbp_rebounds(lu,pg[gid]); rows=rebound_rows(pg[gid]); joined_idx=set(joined.index)
            for idx,jrow in joined.iterrows():
                src=rows.loc[idx]; inf=infer_interval_lineup(lu.events,src)
                if not inf['invariant']: continue
                lc+=1
                if tuple(inf['lineup'])==tuple(int(x) for x in jrow.LINEUP): lc_ok+=1
                else: lc_bad+=1
                if is_player_credited(src.DESCRIPTION): inferred_real=True
                else: inferred_real=consensus_real(lu.events,rebound_candidates(lu.events,src))
                if inferred_real is not None:
                    rc+=1; actual=bool(jrow.NBA_IS_REAL_REBOUND)
                    if bool(inferred_real)==actual: rc_ok+=1
                    else: rc_bad+=1
            detail=[]
            for idx,row in rows.loc[~rows.index.isin(joined_idx)].iterrows():
                inf=infer_interval_lineup(lu.events,row); credited=is_player_credited(row.DESCRIPTION); cands=rebound_candidates(lu.events,row)
                inferred_real=True if credited else consensus_real(lu.events,cands)
                safe=bool(inf['invariant'] and inferred_real is not None)
                target_safe_rows+=int(safe)
                detail.append({'pbp_index':int(idx),'description':str(row.DESCRIPTION),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'player_credited':credited,'interval':inf,'candidate_count':int(len(cands)),'candidate_real_values':sorted({bool(core._nba_real_rebound(lu.events,int(i))) for i in cands.index}),'inferred_real':inferred_real,'safe_candidate':safe})
            allsafe=bool(detail and all(x['safe_candidate'] for x in detail)); target_safe_games+=int(allsafe)
            games.append({'game_id':gid,'status':'OK','unmatched_rows':len(detail),'safe_rows':sum(x['safe_candidate'] for x in detail),'all_unmatched_safe':allsafe,'detail':detail,'join_audit':audit})
        except Exception as exc: games.append({'game_id':gid,'status':'ERROR','error':f'{type(exc).__name__}: {exc}'})
    out={'year':args.year,'target_games':len(targets),'games_ok':sum(g.get('status')=='OK' for g in games),'unmatched_rows':sum(g.get('unmatched_rows',0) for g in games),'safe_rows':target_safe_rows,'games_all_unmatched_safe':target_safe_games,'lineup_control_applicable':lc,'lineup_control_correct':lc_ok,'lineup_control_wrong':lc_bad,'real_control_applicable':rc,'real_control_correct':rc_ok,'real_control_wrong':rc_bad,'games':games}
    args.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({k:v for k,v in out.items() if k!='games'},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
