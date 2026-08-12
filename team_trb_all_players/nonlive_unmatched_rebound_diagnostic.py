#!/usr/bin/env python3
from __future__ import annotations

import argparse,json,re
from pathlib import Path
import pandas as pd

import run_local_treb_production as io
import production_treb_engine_v3 as eng
import local_treb_rebuild as core


def pbp_nonlive(row: pd.Series) -> tuple[bool,str]:
    desc=str(row.get('DESCRIPTION') or '')
    prev=str(row.get('PREV_PBP_DESCRIPTION') or '')
    generic=re.search(r'\(Off:',desc,re.I) is None
    if not generic:
        return False,'player_counter_rebound'
    if re.search(r'Free Throw (?:1 of [23]|2 of 3)|Technical Free Throw|Flagrant Free Throw',prev,re.I):
        return True,'non_live_free_throw_placeholder'
    if re.search(r'Turnover|Violation',prev,re.I):
        return True,'turnover_violation_placeholder'
    if str(row.get('ENDTIME'))=='00:00':
        return True,'buzzer_placeholder'
    if str(row.get('STARTTIME'))==str(row.get('ENDTIME')):
        return True,'zero_clock_span_generic_placeholder'
    return False,'not_proven_nonlive'


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True)
    ap.add_argument('--summary',type=Path,required=True); ap.add_argument('--year',type=int,required=True); ap.add_argument('--chunk-index',type=int,required=True); ap.add_argument('--chunk-size',type=int,default=10); ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    src=json.loads(a.summary.read_text())
    # NBA regular-season game IDs use a three-digit season prefix: 200 for
    # 2000-01, 208 for 2008-09, 223 for 2023-24, etc.  Therefore the start
    # year maps to prefix (year - 1800), not year itself.
    lo=(a.year-1800)*100000; hi=(a.year-1799)*100000
    ids=sorted(int(r['game_id']) for r in src['residual_failures'] if lo<=int(r['game_id'])<hi)
    start=a.chunk_index*a.chunk_size; chunk=ids[start:start+a.chunk_size]
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=eng.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    games=[]
    for gid in chunk:
        rec={'game_id':gid}
        try:
            lu=eng.reconstruct_game_lineups(ng[gid],vg[gid]); _,audit=eng.join_pbp_rebounds(lu,pg[gid])
            unmatched=audit.get('unmatched_rows',[])
            ordered=pg[gid].copy(); ordered['PREV_PBP_DESCRIPTION']=ordered.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
            rb=ordered[ordered.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
            detail=[]; mapped_all=True
            for u in unmatched:
                hit=rb[(rb.PERIOD.eq(u['period'])) & rb.STARTTIME.astype(str).eq(str(u['start_time'])) & rb.ENDTIME.astype(str).eq(str(u['end_time'])) & rb.DESCRIPTION.astype(str).eq(str(u['description']))]
                if len(hit)!=1:
                    mapped_all=False; detail.append({'unmatched':u,'mapped':False,'candidate_rows':len(hit)}); continue
                row=hit.iloc[0]; safe,reason=pbp_nonlive(row); detail.append({'unmatched':u,'mapped':True,'proven_nonlive':safe,'reason':reason,'previous_description':str(row.get('PREV_PBP_DESCRIPTION') or '')})
            safe_all=bool(unmatched and mapped_all and all(x.get('proven_nonlive',False) for x in detail))
            rec.update({'status':'ALL_PBP_PROVEN_NONLIVE' if safe_all else 'UNKNOWN_OR_REAL','unmatched_rows':len(unmatched),'mapped_all':mapped_all,'detail':detail})
        except Exception as exc:
            rec.update({'status':'LINEUP_OR_SOURCE_FAILURE','error':f'{type(exc).__name__}: {exc}'})
        games.append(rec); print(f'NONLIVE_DIAG year={a.year} gid={gid} status={rec["status"]} unmatched={rec.get("unmatched_rows")}',flush=True)
    out={'year':a.year,'chunk_index':a.chunk_index,'season_targets':len(ids),'games':games,'safe_games':sum(g['status']=='ALL_PBP_PROVEN_NONLIVE' for g in games),'safe_rows':sum(g.get('unmatched_rows',0) for g in games if g['status']=='ALL_PBP_PROVEN_NONLIVE'),'status':'COMPLETE'}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({k:v for k,v in out.items() if k!='games'},indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
