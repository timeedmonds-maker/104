#!/usr/bin/env python3
"""Fail-closed target-player recovery with exact team-game invariant adjudication.

Extends the proven target-player interval lane without relaxing any source gate.
Only generic NBA team-rebound rows may have their real/placeholder status toggled,
and only when retained exact team-game integer primitives admit exactly one global
subset of toggles for that game. Player intervals and rebound joins remain exact.
"""
from __future__ import annotations

import argparse,json,pathlib,tempfile,re
from collections import defaultdict
import pandas as pd

import local_treb_rebuild as core
import production_treb_engine_v3 as v3eng
import run_local_treb_production as io
import treb_static_period_unique_recovery as static
import treb_target_player_interval_recovery as base

PV=base.PV; TV=base.TV

def _known_dict(rows):
    return {int(t):r for t,r in rows.items()}

def _team_vectors(joined,nba,teams,abbrs):
    real=joined.IS_REAL_REBOUND.astype(bool); oreb=joined.IS_OREB.astype(bool)
    masks={}; vals={}
    for t in teams:
        off=~joined.OPPONENT.astype(str).eq(abbrs[t]); deff=~off; masks[t]=(off,deff)
        vals[t]=[
            int((off&oreb).sum()),
            int((deff&real&~oreb).sum()),
            int((deff&oreb).sum()),
            int((off&real&~oreb).sum()),
        ]
    return masks,vals

def adjudicate_real_status(joined,nba,teams,abbrs,known):
    """Use retained exact team integer totals only if they imply one unique toggle set."""
    if not known:
        return joined,{'status':'NO_EXACT_TEAM_INVARIANT','toggles':0}
    masks,current=_team_vectors(joined,nba,teams,abbrs)
    order=[]; target=[]
    for t in sorted(known):
        if t not in current: continue
        order.append(t)
        r=known[t]
        target.extend([int(float(getattr(r,f))) for f in TV])
    if not order:
        return joined,{'status':'NO_APPLICABLE_EXACT_TEAM_INVARIANT','toggles':0}
    cur=[]
    for t in order: cur.extend(current[t])
    need=tuple(a-b for a,b in zip(target,cur))
    if all(x==0 for x in need):
        return joined,{'status':'ALREADY_MATCHES_EXACT_TEAM_INVARIANT','toggles':0,'need':list(need)}

    # Eligible uncertainty is deliberately tiny: generic team-rebound rows only.
    # Player-attributed rebounds are never toggled. OREB classification is never altered.
    cands=[]
    for idx,r in joined.iterrows():
        ni=int(r.NBA_INDEX); nr=nba.loc[ni]
        pid=int(nr.PLAYER1_ID) if pd.notna(nr.PLAYER1_ID) else 0
        team_rebound=(pid==0 or pid>=core.PLAYER_MAX)
        generic=not bool(re.search(r'\(Off:',str(r.DESCRIPTION),flags=re.I))
        if not team_rebound or not generic or bool(r.IS_OREB): continue
        old=bool(r.IS_REAL_REBOUND)
        trial=joined.copy(); trial.loc[idx,'IS_REAL_REBOUND']=not old
        _,tv=_team_vectors(trial,nba,teams,abbrs)
        delta=[]
        for t in order:
            delta.extend([tv[t][j]-current[t][j] for j in range(4)])
        d=tuple(delta)
        if any(d): cands.append((int(idx),d,old))

    # Exact capped-count subset DP. We accept only one subset producing the invariant vector.
    states={tuple(0 for _ in need):(1,())}
    for idx,d,old in cands:
        nxt=dict(states)
        for v,(count,path) in states.items():
            nv=tuple(v[i]+d[i] for i in range(len(v)))
            if any(abs(nv[i])>abs(need[i])+2 for i in range(len(v))): continue
            if nv in nxt:
                oc,op=nxt[nv]; nxt[nv]=(min(2,oc+count),op)
            else:
                nxt[nv]=(count,path+(idx,))
        states=nxt
    count,path=states.get(need,(0,()))
    if count!=1:
        return joined,{'status':'INVARIANT_NOT_UNIQUE','toggles':0,'need':list(need),'eligible_candidates':len(cands),'solutions_capped':int(count)}
    out=joined.copy()
    for idx in path: out.loc[idx,'IS_REAL_REBOUND']=not bool(out.loc[idx,'IS_REAL_REBOUND'])
    _,after=_team_vectors(out,nba,teams,abbrs)
    av=[]
    for t in order: av.extend(after[t])
    if av!=target: raise ValueError('unique invariant adjudication failed post-check')
    return out,{'status':'PASS_UNIQUE_EXACT_TEAM_INVARIANT','toggles':len(path),'need':list(need),'eligible_candidates':len(cands),'indices':list(path)}

def game_facts(nba_raw,v3_raw,pbp,requested_players,known_team_rows):
    nba=base.prepare_nba(nba_raw,v3_raw)
    joined,ja=base.exact_rebound_join(nba,pbp)
    if joined is None: raise ValueError(f'rebound join failed: {ja}')
    pteam=core._player_team(nba); ab=base.team_abbrs(nba); teams=sorted(set(pteam.values()))
    if len(teams)!=2 or any(t not in ab for t in teams): raise ValueError('team identity/abbreviation unresolved')
    joined,ia=adjudicate_real_status(joined,nba,teams,ab,known_team_rows)
    real=joined.IS_REAL_REBOUND.astype(bool);oreb=joined.IS_OREB.astype(bool)
    masks={}; tr=[]
    for t in teams:
        off=~joined.OPPONENT.astype(str).eq(ab[t]);deff=~off;masks[t]=(off,deff)
        tr.append({'game_id':int(nba.GAME_ID.iloc[0]),'team_id':int(t),'team_oreb':int((off&oreb).sum()),'team_dreb':int((deff&real&~oreb).sum()),'opponent_oreb':int((deff&oreb).sum()),'opponent_dreb':int((off&real&~oreb).sum())})
    pr=[]
    for t,p in requested_players:
        pid=int(p);t=int(t)
        if pteam.get(pid)!=t: continue
        st=base.player_state(nba,pid)
        if st is None: continue
        sec,on_by,evidence=st
        on=pd.Series([bool(on_by.get(int(i),False)) for i in joined.NBA_INDEX],index=joined.index)
        off,deff=masks[t]
        pr.append({'game_id':int(nba.GAME_ID.iloc[0]),'team_id':t,'player_id':base.sid(pid),'seconds_on':sec,'team_oreb_on':int((on&off&oreb).sum()),'team_dreb_on':int((on&deff&real&~oreb).sum()),'opponent_oreb_on':int((on&deff&oreb).sum()),'opponent_dreb_on':int((on&off&real&~oreb).sum()),'interval_evidence':json.dumps(evidence,separators=(',',':'))})
    return tr,pr,ja,ia

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--season',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);season=a.season;year=season[:4]
    reg=pd.read_csv(static.pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),low_memory=False);target_games,np,nt=static.targets(reg,season)
    pc,tc=static.control_frames(cur,season)
    pmap={(base.gid(r.game_id),int(r.team_id),base.sid(r.player_id)):r for r in pc.itertuples(index=False)}
    tmap={(base.gid(r.game_id),int(r.team_id)):r for r in tc.itertuples(index=False)}
    control_games=set(g for g,_,_ in pmap)|set(g for g,_ in tmap); wanted=set(target_games)|control_games
    qa={'status':'PASS','season':season,'target_games':len(target_games),'control_games_requested':len(control_games),'controls_checked':0,'control_mismatches':0,'control_games_reconstructed':0,'target_games_with_candidates':0,'recovered_player_primitives':0,'recovered_team_primitives':0,'invariant_games':0,'invariant_toggles':0,'game_failures':[],'integrity':{'player_interval_period_local':True,'exact_rebound_identity_or_unique_period_assignment_only':True,'event_reuse_forbidden':True,'only_generic_team_rebound_real_status_adjudicated':True,'exact_team_integer_invariant_required':True,'unique_subset_required':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_team_subtraction_used':False,'promotion_performed':False}}
    if not target_games:
        qa['status']='NO_TARGETS';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');return 0
    cp=[];ct=[]
    with tempfile.TemporaryDirectory(prefix='treb_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_arc_') as td:
        try:
            repo=static.prep(pathlib.Path(gd));tmp=pathlib.Path(td)
            nr,_=static.archive_df(repo,tmp,'nbastats',year,wanted);vr,_=static.archive_df(repo,tmp,'nbastatsv3',year,wanted);pr,_=static.archive_df(repo,tmp,'pbpstats',year,wanted)
        except Exception as e:
            qa['status']='SOURCE_FAILURE';qa['game_failures'].append({'scope':'source','error':f'{type(e).__name__}: {e}'});(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
    nba=io.normalize_nba(nr);v3=v3eng.normalize_v3(vr);pbp=io.normalize_pbp(pr)
    ng={base.gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={base.gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={base.gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    for g in sorted(wanted):
        role='target' if g in target_games else 'control'
        if g not in ng or g not in vg or g not in pg:
            qa['game_failures'].append({'game_id':g,'role':role,'status':'SOURCE_SET_GAP'});continue
        req=set(np.get(g,set()))|{(t,p) for gg,t,p in pmap if gg==g}
        known={t:r for (gg,t),r in tmap.items() if gg==g}
        try: tr,pl,ja,ia=game_facts(ng[g],vg[g],pg[g],req,known)
        except Exception as e:
            qa['game_failures'].append({'game_id':g,'role':role,'status':'RECONSTRUCTION_FAIL','error':f'{type(e).__name__}: {e}'});continue
        if ia.get('status')=='PASS_UNIQUE_EXACT_TEAM_INVARIANT': qa['invariant_games']+=1;qa['invariant_toggles']+=int(ia.get('toggles',0))
        fp={(int(z['team_id']),base.sid(z['player_id'])):z for z in pl};ft={int(z['team_id']):z for z in tr};mm=[];checked=0
        for k,z in fp.items():
            old=pmap.get((g,k[0],k[1]))
            if old is not None:
                checked+=1;qa['controls_checked']+=1
                if not base.eq(old,z,PV): mm.append({'kind':'player','key':[g,k[0],k[1]],'old':[float(getattr(old,f)) for f in PV],'new':[float(z[f]) for f in PV]})
        for t,z in ft.items():
            old=tmap.get((g,t))
            if old is not None:
                checked+=1;qa['controls_checked']+=1
                if not base.eq(old,z,TV): mm.append({'kind':'team','key':[g,t],'old':[float(getattr(old,f)) for f in TV],'new':[float(z[f]) for f in TV]})
        if mm:
            qa['control_mismatches']+=len(mm);qa['game_failures'].append({'game_id':g,'role':role,'status':'CONTROL_MISMATCH','invariant':ia,'examples':mm[:10]});continue
        if checked: qa['control_games_reconstructed']+=1
        if role!='target': continue
        got=0;prov=f'exact target-player intervals + unique exact team-game rebound invariant adjudication @ {static.UPSTREAM_COMMIT}'
        for k in sorted(np.get(g,set())):
            if k in fp:
                z=dict(fp[k]);z['season']=season;z['provenance']=prov;cp.append(z);got+=1
        for t in sorted(nt.get(g,set())):
            if t in ft:
                z=dict(ft[t]);z['season']=season;z['provenance']=prov;ct.append(z);got+=1
        if got:qa['target_games_with_candidates']+=1
    if qa['control_mismatches']:
        qa['status']='FAIL_CONTROL_MISMATCH';cp=[];ct=[]
    elif qa['game_failures']:qa['status']='PARTIAL'
    if cp:pd.DataFrame(cp).to_csv(out/'PLAYER_CANDIDATES.csv.gz',index=False,compression='gzip')
    if ct:pd.DataFrame(ct).to_csv(out/'TEAM_CANDIDATES.csv.gz',index=False,compression='gzip')
    qa['recovered_player_primitives']=len(cp);qa['recovered_team_primitives']=len(ct)
    (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2),flush=True);return 0

if __name__=='__main__':raise SystemExit(main())
