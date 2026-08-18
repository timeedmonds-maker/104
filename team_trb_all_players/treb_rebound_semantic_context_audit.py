#!/usr/bin/env python3
"""Diagnostic-only neighbouring-event enrichment for proven TREB control mismatches.

Consumes the persisted 23 exact-control mismatches, re-fetches only their static NBA/PBP/V3
source games, and preserves raw PBP/NBA fields needed to distinguish live team rebounds from
dead-ball bookkeeping rebounds. This lane never promotes values. Any rule derived here still
requires a fresh broad zero-mismatch exact-control gate before promotion.
"""
from __future__ import annotations
import argparse,json,pathlib,tempfile
import pandas as pd
import production_treb_engine_v3 as v3eng
import run_local_treb_production as io
import treb_static_period_unique_recovery as static
import treb_target_player_interval_recovery as base
import treb_rebound_semantic_audit as sem
import local_treb_rebuild as core

def _safe_int(v):
    try:return int(v) if pd.notna(v) else 0
    except Exception:return 0

def _ctx(r,prefix):
    if r is None:
        return {f'{prefix}_eventnum':0,f'{prefix}_elapsed':-1,f'{prefix}_description':'',
                f'{prefix}_eventmsgtype':0,f'{prefix}_actiontype':0,
                f'{prefix}_player1_id':0,f'{prefix}_player2_id':0,f'{prefix}_player3_id':0,
                f'{prefix}_player1_team_id':0,f'{prefix}_player2_team_id':0,f'{prefix}_player3_team_id':0}
    return {f'{prefix}_eventnum':_safe_int(r.get('EVENTNUM')),f'{prefix}_elapsed':_safe_int(r.get('ELAPSED')),
            f'{prefix}_description':str(r.get('DESCRIPTION_NORM','')),
            f'{prefix}_eventmsgtype':_safe_int(r.get('EVENTMSGTYPE')),f'{prefix}_actiontype':_safe_int(r.get('EVENTMSGACTIONTYPE')),
            f'{prefix}_player1_id':_safe_int(r.get('PLAYER1_ID')),f'{prefix}_player2_id':_safe_int(r.get('PLAYER2_ID')),f'{prefix}_player3_id':_safe_int(r.get('PLAYER3_ID')),
            f'{prefix}_player1_team_id':_safe_int(r.get('PLAYER1_TEAM_ID')),f'{prefix}_player2_team_id':_safe_int(r.get('PLAYER2_TEAM_ID')),f'{prefix}_player3_team_id':_safe_int(r.get('PLAYER3_TEAM_ID'))}

def enrich(rows,nba,joined):
    ordered=nba.sort_values(['PERIOD','ELAPSED','EVENTNUM'],kind='stable');idxs=ordered.index.tolist();pos={idx:i for i,idx in enumerate(idxs)}
    for rec in rows:
        i=pos.get(rec['nba_index']);prev=ordered.iloc[i-1] if i is not None and i>0 else None;nxt=ordered.iloc[i+1] if i is not None and i+1<len(ordered) else None;nxt2=ordered.iloc[i+2] if i is not None and i+2<len(ordered) else None
        rec.update(_ctx(prev,'prev_nba'));rec.update(_ctx(nxt,'next_nba'));rec.update(_ctx(nxt2,'next2_nba'))
        cur=nba.loc[int(rec['nba_index'])]
        rec['nba_eventmsgtype']=_safe_int(cur.get('EVENTMSGTYPE'));rec['nba_actiontype']=_safe_int(cur.get('EVENTMSGACTIONTYPE'))
        rec['nba_player1_team_id']=_safe_int(cur.get('PLAYER1_TEAM_ID'));rec['nba_player2_team_id']=_safe_int(cur.get('PLAYER2_TEAM_ID'));rec['nba_player3_team_id']=_safe_int(cur.get('PLAYER3_TEAM_ID'))
        jr=joined.loc[int(rec['joined_index'])]
        rec['pbp_start_time']=str(jr.get('STARTTIME',''));rec['pbp_end_time']=str(jr.get('ENDTIME',''))
        rec['pbp_possession_id']=str(jr.get(core.POSSESSION_ID,''));rec['pbp_offensive_rebounds']=_safe_int(jr.get('OFFENSIVEREBOUNDS'))
        rec['pbp_start_equals_end']=bool(str(jr.get('STARTTIME',''))==str(jr.get('ENDTIME','')))
        rec['next_same_clock']=bool(nxt is not None and _safe_int(nxt.get('ELAPSED'))==int(rec.get('nba_elapsed',0)))
        rec['next_elapsed_delta']=(_safe_int(nxt.get('ELAPSED'))-int(rec.get('nba_elapsed',0))) if nxt is not None else -1
    return rows

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mismatch-path',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--season',required=True);a=ap.parse_args()
    out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);season=a.season;year=season[:4]
    mm=pd.read_csv(a.mismatch_path,low_memory=False);mm=mm[mm.season.astype(str).eq(season)].copy()
    qa={'status':'PASS_DIAGNOSTIC','season':season,'persisted_mismatches':len(mm),'mismatch_games':0,'source_failures':0,'reconstruction_failures':0,'event_rows':0,'promotion_performed':False,'integrity':{'diagnostic_only':True,'uses_previously_proven_exact_control_mismatches':True,'authoritative_checkpoint_required_before_any_promotion':True,'raw_semantic_fields_preserved':True}}
    if mm.empty:
        qa['status']='NO_MISMATCHES';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
    games={base.gid(x) for x in mm.game_id};qa['mismatch_games']=len(games)
    try:
        with tempfile.TemporaryDirectory(prefix='treb_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_arc_') as td:
            repo=static.prep(pathlib.Path(gd));tmp=pathlib.Path(td)
            nr,_=static.archive_df(repo,tmp,'nbastats',year,games);vr,_=static.archive_df(repo,tmp,'nbastatsv3',year,games);pr,_=static.archive_df(repo,tmp,'pbpstats',year,games)
    except Exception as e:
        qa['status']='SOURCE_FAILURE';qa['source_failures']=1;qa['source_error']=f'{type(e).__name__}: {e}';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
    nba=io.normalize_nba(nr);v3=v3eng.normalize_v3(vr);pbp=io.normalize_pbp(pr)
    ng={base.gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={base.gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={base.gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    events=[]
    for g in sorted(games):
        if g not in ng or g not in vg or g not in pg:qa['reconstruction_failures']+=1;continue
        try:
            prepared=base.prepare_nba(ng[g],vg[g]);joined,ja=base.exact_rebound_join(prepared,pg[g])
            if joined is None:raise ValueError(str(ja))
            pteam=core._player_team(prepared);abbrs=base.team_abbrs(prepared)
        except Exception:qa['reconstruction_failures']+=1;continue
        for r in mm[mm.game_id.map(base.gid).eq(g)].to_dict('records'):
            t=int(r['team_id']);comp=str(r['component']);kind=str(r['kind']);pid=base.sid(r.get('player_id','')) if kind=='player' else ''
            on=None;basecomp=comp
            if kind=='player':
                st=base.player_state(prepared,int(pid))
                if st is None or pteam.get(int(pid))!=t:qa['reconstruction_failures']+=1;continue
                _,on_by,_=st;on=pd.Series([bool(on_by.get(int(i),False)) for i in joined.NBA_INDEX],index=joined.index);basecomp=comp.removesuffix('_on')
            rows=sem.component_rows(joined,prepared,abbrs[t],t,basecomp,on)
            for e in enrich(rows,prepared,joined):
                e.update({'season':season,'game_id':g,'kind':kind,'team_id':t,'player_id':pid,'component':comp,'exact':r.get('exact'),'reconstructed':r.get('reconstructed'),'delta':r.get('delta')});events.append(e)
    qa['event_rows']=len(events);mm.to_csv(out/'MISMATCHES.csv.gz',index=False,compression='gzip')
    if events:pd.DataFrame(events).to_csv(out/'MISMATCH_COMPONENT_EVENTS.csv.gz',index=False,compression='gzip')
    (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
