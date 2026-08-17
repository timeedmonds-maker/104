#!/usr/bin/env python3
"""Diagnostic-only audit for the remaining exact-control rebound semantic mismatches.

This lane never emits promotable primitives. It reconstructs exact control games with
the proven target-player interval engine, records every mismatch, and preserves the
individual paired PBP/NBA rebound rows that can affect the mismatched primitive so a
subsequent rule may be derived only from explicit source semantics and then globally
zero-mismatch validated.
"""
from __future__ import annotations
import argparse,json,pathlib,tempfile,re
import pandas as pd
import production_treb_engine_v3 as v3eng
import run_local_treb_production as io
import treb_static_period_unique_recovery as static
import treb_target_player_interval_recovery as base
import local_treb_rebuild as core

PV=base.PV;TV=base.TV

def fval(r,k): return float(getattr(r,k))
def component_rows(joined,nba,abbr,team_id,component,on=None):
    off=~joined.OPPONENT.astype(str).eq(abbr); deff=~off
    real=joined.IS_REAL_REBOUND.astype(bool); oreb=joined.IS_OREB.astype(bool)
    mask={'team_oreb':off&oreb,'team_dreb':deff&real&~oreb,'opponent_oreb':deff&oreb,'opponent_dreb':off&real&~oreb}[component]
    if on is not None: mask=mask&on
    out=[]
    for idx,r in joined[mask].iterrows():
        ni=int(r.NBA_INDEX); nr=nba.loc[ni]
        desc=str(r.get('DESCRIPTION','')); prev=str(r.get('PREV_PBP_DESCRIPTION',''))
        ndesc=str(nr.get('DESCRIPTION_NORM',''))
        pid=int(nr.PLAYER1_ID) if pd.notna(nr.PLAYER1_ID) else 0
        out.append({
            'joined_index':int(idx),'nba_index':ni,'period':int(r.PERIOD),
            'nba_eventnum':int(nr.EVENTNUM),'nba_elapsed':int(nr.ELAPSED),
            'pbp_description':desc,'pbp_prev_description':prev,'nba_description':ndesc,
            'opponent':str(r.OPPONENT),'is_oreb':bool(r.IS_OREB),'is_real_rebound':bool(r.IS_REAL_REBOUND),
            'nba_is_real_rebound':bool(r.get('NBA_IS_REAL_REBOUND',False)),'nba_player1_id':pid,
            'generic_team_rebound':bool(pid==0 or pid>=core.PLAYER_MAX),
            'contains_counter':bool(re.search(r'\(off:\d+ def:\d+\)',desc,re.I)),
        })
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--season',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);season=a.season;year=season[:4]
    pc,tc=static.control_frames(cur,season)
    pmap={(base.gid(r.game_id),int(r.team_id),base.sid(r.player_id)):r for r in pc.itertuples(index=False)}
    tmap={(base.gid(r.game_id),int(r.team_id)):r for r in tc.itertuples(index=False)}
    games=set(g for g,_,_ in pmap)|set(g for g,_ in tmap)
    qa={'status':'PASS_DIAGNOSTIC','season':season,'control_games_requested':len(games),'control_games_reconstructed':0,'controls_checked':0,'control_mismatches':0,'source_failures':0,'reconstruction_failures':0,'mismatch_games':0,'promotion_performed':False,'integrity':{'diagnostic_only':True,'exact_control_rows_only':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_team_subtraction_used':False}}
    if not games:
        qa['status']='NO_CONTROLS';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');return 0
    try:
        with tempfile.TemporaryDirectory(prefix='treb_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_arc_') as td:
            repo=static.prep(pathlib.Path(gd));tmp=pathlib.Path(td)
            nr,_=static.archive_df(repo,tmp,'nbastats',year,games);vr,_=static.archive_df(repo,tmp,'nbastatsv3',year,games);pr,_=static.archive_df(repo,tmp,'pbpstats',year,games)
    except Exception as e:
        qa['status']='SOURCE_FAILURE';qa['source_failures']=1;qa['source_error']=f'{type(e).__name__}: {e}';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
    nba=io.normalize_nba(nr);v3=v3eng.normalize_v3(vr);pbp=io.normalize_pbp(pr)
    ng={base.gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={base.gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={base.gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    mismatches=[];events=[]
    for g in sorted(games):
        if g not in ng or g not in vg or g not in pg:
            qa['reconstruction_failures']+=1;continue
        try:
            prepared=base.prepare_nba(ng[g],vg[g]);joined,ja=base.exact_rebound_join(prepared,pg[g])
            if joined is None: raise ValueError(str(ja))
            pteam=core._player_team(prepared);abbrs=base.team_abbrs(prepared);teams=sorted(set(pteam.values()))
            if len(teams)!=2 or any(t not in abbrs for t in teams): raise ValueError('team identity unresolved')
        except Exception:
            qa['reconstruction_failures']+=1;continue
        qa['control_games_reconstructed']+=1
        real=joined.IS_REAL_REBOUND.astype(bool);oreb=joined.IS_OREB.astype(bool)
        team_new={}
        for t in teams:
            off=~joined.OPPONENT.astype(str).eq(abbrs[t]);deff=~off
            team_new[t]={'team_oreb':int((off&oreb).sum()),'team_dreb':int((deff&real&~oreb).sum()),'opponent_oreb':int((deff&oreb).sum()),'opponent_dreb':int((off&real&~oreb).sum())}
        game_bad=False
        for t,z in team_new.items():
            old=tmap.get((g,t))
            if old is None: continue
            qa['controls_checked']+=1
            for comp in TV:
                ov=int(round(fval(old,comp)));nv=int(z[comp])
                if ov!=nv:
                    game_bad=True;qa['control_mismatches']+=1
                    mid=len(mismatches);mismatches.append({'mismatch_id':mid,'season':season,'game_id':g,'kind':'team','team_id':t,'player_id':'','component':comp,'exact':ov,'reconstructed':nv,'delta':nv-ov})
                    for e in component_rows(joined,prepared,abbrs[t],t,comp): e.update({'mismatch_id':mid,'season':season,'game_id':g,'kind':'team','team_id':t,'player_id':'','component':comp});events.append(e)
        for (gg,t,pid),old in pmap.items():
            if gg!=g or pteam.get(int(pid))!=t: continue
            st=base.player_state(prepared,int(pid))
            if st is None: continue
            sec,on_by,_=st;on=pd.Series([bool(on_by.get(int(i),False)) for i in joined.NBA_INDEX],index=joined.index)
            off=~joined.OPPONENT.astype(str).eq(abbrs[t]);deff=~off
            z={'seconds_on':sec,'team_oreb_on':int((on&off&oreb).sum()),'team_dreb_on':int((on&deff&real&~oreb).sum()),'opponent_oreb_on':int((on&deff&oreb).sum()),'opponent_dreb_on':int((on&off&real&~oreb).sum())}
            qa['controls_checked']+=1
            for comp in PV:
                ov=int(round(fval(old,comp)));nv=int(z[comp])
                if ov!=nv:
                    game_bad=True;qa['control_mismatches']+=1
                    mid=len(mismatches);mismatches.append({'mismatch_id':mid,'season':season,'game_id':g,'kind':'player','team_id':t,'player_id':pid,'component':comp,'exact':ov,'reconstructed':nv,'delta':nv-ov})
                    if comp!='seconds_on':
                        basecomp=comp.removesuffix('_on')
                        for e in component_rows(joined,prepared,abbrs[t],t,basecomp,on): e.update({'mismatch_id':mid,'season':season,'game_id':g,'kind':'player','team_id':t,'player_id':pid,'component':comp});events.append(e)
        if game_bad:qa['mismatch_games']+=1
    if mismatches:pd.DataFrame(mismatches).to_csv(out/'MISMATCHES.csv.gz',index=False,compression='gzip')
    if events:pd.DataFrame(events).to_csv(out/'MISMATCH_COMPONENT_EVENTS.csv.gz',index=False,compression='gzip')
    (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
