#!/usr/bin/env python3
"""Prove exact rebound-event identity by unique chronological assignment.

Diagnostic only. Starting from the CURRENT production v3 rebound join, all
still-unmatched PBP rebounds in a period are considered jointly. Already-used
NBA rebound rows are reserved. An assignment is legal only when every unmatched
PBP row maps to a distinct unused NBA EVENTMSGTYPE=4 row inside its existing
±5-second legal window and NBA event order is strictly increasing with PBP order.
A period is repairable only when exactly one complete assignment exists.

Synthetic rows already resolved by production v3 remain resolved and are never
used as leave-one-out NBA-event truth anchors because they have no NBA_INDEX.
"""
from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
import pandas as pd
import local_treb_rebuild as core
import production_rebound_v3 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io


def rebound_rows(pbp_game:pd.DataFrame):
    ordered=pbp_game.copy(); ordered['PREV_PBP_DESCRIPTION']=ordered.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    rows=ordered[ordered.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    rows['START_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rows.PERIOD,rows.STARTTIME)]
    rows['END_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rows.PERIOD,rows.ENDTIME)]
    return rows


def enumerate_assignments(candidate_orders:list[list[int]],limit:int=2):
    found=[]
    def rec(i,last,path):
        if len(found)>=limit:return
        if i==len(candidate_orders): found.append(tuple(path)); return
        for order in candidate_orders[i]:
            if order<=last: continue
            rec(i+1,order,path+[order])
            if len(found)>=limit:return
    rec(0,-1,[])
    return found


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--residual',type=Path,required=True); ap.add_argument('--year',type=int,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    ids=sorted({int(r['game_id']) for r in json.loads(args.residual.read_text())})
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba[nba.GAME_ID.isin(ids)].groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3[v3.gameId.isin(ids)].groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp[pbp.GAMEID.isin(ids)].groupby('GAMEID',sort=False)}
    games=[]; control_applicable=control_correct=control_wrong=0
    for gid in ids:
        try:
            lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,audit=rebound.join_pbp_rebounds(lu,pg[gid]); rows=rebound_rows(pg[gid]); nba_ev=lu.events
        except Exception as exc:
            games.append({'game_id':gid,'status':'LINEUP_OR_JOIN_ERROR','error':f'{type(exc).__name__}: {exc}'}); continue
        if int(audit.get('unmatched_rebound_bearing_rows',0))==0:
            games.append({'game_id':gid,'status':'NO_UNMATCHED','unmatched_rows':0}); continue
        numeric_idx=pd.to_numeric(joined.NBA_INDEX,errors='coerce').dropna().astype(int)
        counts=Counter(int(x) for x in numeric_idx); used=set(counts)
        period_results=[]; all_periods_unique=True; total_unmatched=0; total_assigned=0
        for period,prows in rows.groupby('PERIOD',sort=False):
            pidx=list(prows.index); unmatched=[idx for idx in pidx if idx not in joined.index]
            if not unmatched: continue
            total_unmatched+=len(unmatched)
            pevents=nba_ev[nba_ev.PERIOD.eq(period)].sort_values(['ELAPSED','EVENTNUM'],kind='stable')
            order_to_index={o:int(idx) for o,idx in enumerate(pevents.index)}; index_to_order={idx:o for o,idx in order_to_index.items()}
            candidate_orders=[]; candidate_detail=[]
            for idx in unmatched:
                row=rows.loc[idx]
                c=pevents[pevents.EVENTMSGTYPE.eq(4)&pevents.ELAPSED.gt(int(row.START_ELAPSED)-5)&pevents.ELAPSED.lt(int(row.END_ELAPSED)+5)&~pevents.index.isin(used)]
                orders=sorted(index_to_order[int(x)] for x in c.index); candidate_orders.append(orders)
                candidate_detail.append({'pbp_index':int(idx),'description':str(row.DESCRIPTION),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'candidates':[{'nba_index':int(x),'order':int(index_to_order[int(x)]),'eventnum':int(pevents.loc[x,'EVENTNUM']),'elapsed':int(pevents.loc[x,'ELAPSED']),'description':str(pevents.loc[x,'DESCRIPTION_NORM']),'lineup':[int(p) for p in pevents.loc[x,'LINEUP']],'real':bool(core._nba_real_rebound(nba_ev,int(x)))} for x in c.index]})
            sols=enumerate_assignments(candidate_orders,limit=2)
            unique=len(sols)==1
            assignments=[]
            if unique:
                for idx,order in zip(unmatched,sols[0]):
                    ni=order_to_index[order]; assignments.append({'pbp_index':int(idx),'nba_index':int(ni),'eventnum':int(nba_ev.loc[ni,'EVENTNUM']),'elapsed':int(nba_ev.loc[ni,'ELAPSED']),'lineup':[int(x) for x in nba_ev.loc[ni,'LINEUP']],'real':bool(core._nba_real_rebound(nba_ev,ni))})
                total_assigned+=len(assignments)
            else: all_periods_unique=False
            period_results.append({'period':int(period),'unmatched_rows':len(unmatched),'solution_count_capped':len(sols),'unique_complete_assignment':unique,'candidate_detail':candidate_detail,'assignments':assignments})
        games.append({'game_id':gid,'status':'OK','unmatched_rows':total_unmatched,'assigned_rows':total_assigned,'all_unmatched_uniquely_assigned':bool(total_unmatched and all_periods_unique and total_assigned==total_unmatched),'periods':period_results,'join_audit':audit})

        # Leave-one-out control: only one-to-one, real NBA-indexed matched rows are truth anchors.
        for idx,jrow in joined.iterrows():
            if pd.isna(jrow.NBA_INDEX): continue
            actual=int(jrow.NBA_INDEX)
            if counts[actual]!=1: continue
            row=rows.loc[idx]; c=nba_ev[nba_ev.PERIOD.eq(row.PERIOD)&nba_ev.EVENTMSGTYPE.eq(4)&nba_ev.ELAPSED.gt(int(row.START_ELAPSED)-5)&nba_ev.ELAPSED.lt(int(row.END_ELAPSED)+5)&~nba_ev.index.isin(used-{actual})]
            if len(c)!=1: continue
            control_applicable+=1; forced=int(c.index[0])
            if forced==actual: control_correct+=1
            else: control_wrong+=1
    ok=[g for g in games if g.get('status')=='OK']
    out={'diagnostic_engine':'production_rebound_v3','year':args.year,'targets':len(ids),'games_analyzed':len(ok),'games_all_unmatched_uniquely_assigned':sum(g.get('all_unmatched_uniquely_assigned',False) for g in ok),'unmatched_rows':sum(g.get('unmatched_rows',0) for g in ok),'uniquely_assigned_rows_in_fully_unique_periods':sum(g.get('assigned_rows',0) for g in ok),'control_applicable':control_applicable,'control_correct':control_correct,'control_wrong':control_wrong,'games':games}
    args.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({k:v for k,v in out.items() if k!='games'},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
