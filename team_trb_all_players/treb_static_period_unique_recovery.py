#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,pathlib,subprocess,tarfile,tempfile
from collections import defaultdict
import pandas as pd
import build_exact_game_fact_layer as exact
import production_treb_engine_v3 as v3eng
import production_rebound_period_unique_candidate as period_unique
import run_local_treb_production as io

UPSTREAM_COMMIT='e829d4678be1e075f99e5d41a1c5f97089be446b'
UPSTREAM='https://github.com/shufinskiy/nba_data.git'
PV=['seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on']
TV=['team_oreb','team_dreb','opponent_oreb','opponent_dreb']
def gid(x): return int(float(str(x).strip()))
def sid(x): return str(x).strip().removesuffix('.0')
def pick(root,name):
    h=list(pathlib.Path(root).rglob(name))
    if not h: raise FileNotFoundError(name)
    return h[0]
def prep(root):
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'remote','add','origin',UPSTREAM],check=True)
    subprocess.run(['git','-C',str(root),'-c','http.version=HTTP/1.1','fetch','--depth=1','--no-tags','origin',UPSTREAM_COMMIT],check=True,timeout=600)
    return root
def archive_df(repo,tmp,kind,year,wanted):
    a=tmp/f'{kind}_{year}.tar.xz'; spec=f'{UPSTREAM_COMMIT}:datasets/{kind}_{year}.tar.xz'
    with a.open('wb') as f: subprocess.run(['git','-C',str(repo),'show',spec],stdout=f,check=True,timeout=600)
    with tarfile.open(a,'r:xz') as tf:
        ms=[m for m in tf.getmembers() if m.isfile() and m.name.lower().endswith('.csv')]
        if not ms: raise RuntimeError(f'no csv in {kind}_{year}')
        fh=tf.extractfile(ms[0]); df=pd.read_csv(fh,low_memory=False)
    a.unlink(missing_ok=True)
    gc=next((c for c in df.columns if c.upper().replace('_','')=='GAMEID'),None)
    if not gc: raise RuntimeError(f'no game id in {kind}_{year}')
    nums=pd.to_numeric(df[gc],errors='coerce')
    return df[nums.isin(wanted)].copy(),spec
def targets(reg,season):
    rows=reg[reg.season.astype(str).eq(season)]; games=set(); ps=defaultdict(set); ts=defaultdict(set)
    for r in rows.to_dict('records'):
        g=gid(r['game_id']);games.add(g)
        p=str(r.get('player_targets','') or '')
        if p.lower()!='nan':
            for tok in p.split('|'):
                if ':' in tok:
                    t,x=tok.split(':',1);ps[g].add((int(float(t)),sid(x)))
        t=str(r.get('team_ids','') or '')
        if t.lower()!='nan':
            for tok in t.split('|'):
                if tok: ts[g].add(int(float(tok)))
    return games,ps,ts
def control_frames(cur,season):
    p=pd.read_csv(pick(cur,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'),low_memory=False)
    t=pd.read_csv(pick(cur,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'),low_memory=False)
    p=p[p.season.astype(str).eq(season)].copy() if 'season' in p else p.iloc[0:0]
    t=t[t.season.astype(str).eq(season)].copy() if 'season' in t else t.iloc[0:0]
    return p,t
def eq(old,new,fs):
    try:return all(float(getattr(old,f))==float(new[f]) for f in fs)
    except:return False
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--season',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);season=a.season;year=season[:4]
    reg=pd.read_csv(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),low_memory=False);target_games,np,nt=targets(reg,season)
    pc,tc=control_frames(cur,season)
    control_games=(set(map(gid,pc.game_id.unique()))|set(map(gid,tc.game_id.unique())))
    wanted=set(target_games)|control_games
    qa={'status':'PASS','season':season,'upstream_commit':UPSTREAM_COMMIT,'target_games':len(target_games),'control_games_requested':len(control_games),'required_player_primitives':sum(map(len,np.values())),'required_team_primitives':sum(map(len,nt.values())),'controls_checked':0,'control_mismatches':0,'control_games_reconstructed':0,'target_games_reconstructed':0,'period_unique_repairs':0,'recovered_player_primitives':0,'recovered_team_primitives':0,'game_failures':[],'sources':{},'integrity':{'period_local_only':True,'unique_order_preserving_injection_required':True,'event_reuse_forbidden':True,'nearest_neighbor_guess_used':False,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_team_subtraction_used':False,'promotion_performed':False}}
    if not target_games:
        qa['status']='NO_TARGETS';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');return 0
    pmap={(gid(r.game_id),int(r.team_id),sid(r.player_id)):r for r in pc.itertuples(index=False)}
    tmap={(gid(r.game_id),int(r.team_id)):r for r in tc.itertuples(index=False)}
    cp=[];ct=[]
    with tempfile.TemporaryDirectory(prefix='treb_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_arc_') as td:
        try:
            repo=prep(pathlib.Path(gd));tmp=pathlib.Path(td)
            nr,qa['sources']['nbastats']=archive_df(repo,tmp,'nbastats',year,wanted)
            vr,qa['sources']['nbastatsv3']=archive_df(repo,tmp,'nbastatsv3',year,wanted)
            pr,qa['sources']['pbpstats']=archive_df(repo,tmp,'pbpstats',year,wanted)
        except Exception as e:
            qa['status']='SOURCE_FAILURE';qa['game_failures'].append({'scope':'source','error':f'{type(e).__name__}: {e}'});(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
        nba=io.normalize_nba(nr);v3=v3eng.normalize_v3(vr);pbp=io.normalize_pbp(pr)
        ng={gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
        exact.rebound_join_engine.join_pbp_rebounds=period_unique.join_pbp_rebounds
        for g in sorted(wanted):
            role='target' if g in target_games else 'control'
            if g not in ng or g not in vg or g not in pg:
                qa['game_failures'].append({'game_id':g,'role':role,'status':'SOURCE_SET_GAP','nba':g in ng,'v3':g in vg,'pbp':g in pg});continue
            try: tr,pl,audit=exact.build_game(g,ng[g],vg[g],pg[g])
            except Exception as e:
                qa['game_failures'].append({'game_id':g,'role':role,'status':'RECONSTRUCTION_FAIL','error':f'{type(e).__name__}: {e}'});continue
            qa['period_unique_repairs']+=int(audit.get('join_audit',{}).get('period_unique_repairs',0))
            mm=[]
            for z in tr:
                k=(g,int(z['team_id']));old=tmap.get(k)
                if old is not None:
                    qa['controls_checked']+=1
                    if not eq(old,z,TV):mm.append({'kind':'team','key':list(k)})
            for z in pl:
                k=(g,int(z['team_id']),sid(z['player_id']));old=pmap.get(k)
                if old is not None:
                    qa['controls_checked']+=1
                    if not eq(old,z,PV):mm.append({'kind':'player','key':list(k)})
            if mm:
                qa['control_mismatches']+=len(mm);qa['game_failures'].append({'game_id':g,'role':role,'status':'CONTROL_MISMATCH','examples':mm[:10]});continue
            if g in control_games: qa['control_games_reconstructed']+=1
            if role!='target': continue
            fp={(int(z['team_id']),sid(z['player_id'])):z for z in pl};ft={int(z['team_id']):z for z in tr};miss=[]
            for k in sorted(np.get(g,set())):
                if k not in fp:miss.append({'kind':'player','team_id':k[0],'player_id':k[1]})
            for t in sorted(nt.get(g,set())):
                if t not in ft:miss.append({'kind':'team','team_id':t})
            if miss:
                qa['game_failures'].append({'game_id':g,'role':role,'status':'TARGET_NOT_RECONSTRUCTED','missing':miss});continue
            qa['target_games_reconstructed']+=1
            prov=f'exact pinned static nba+v3+pbp unique period-local order-preserving reconciliation @ {UPSTREAM_COMMIT}'
            for k in sorted(np.get(g,set())):
                z=dict(fp[k]);z['season']=season;z['player_id']=sid(z['player_id']);z['provenance']=prov;cp.append(z)
            for t in sorted(nt.get(g,set())):
                z=dict(ft[t]);z['season']=season;z['provenance']=prov;ct.append(z)
    if qa['control_mismatches']:
        qa['status']='FAIL_CONTROL_MISMATCH';cp=[];ct=[]
    elif qa['game_failures']:qa['status']='PARTIAL'
    if cp:pd.DataFrame(cp).to_csv(out/'PLAYER_CANDIDATES.csv.gz',index=False,compression='gzip')
    if ct:pd.DataFrame(ct).to_csv(out/'TEAM_CANDIDATES.csv.gz',index=False,compression='gzip')
    qa['recovered_player_primitives']=len(cp);qa['recovered_team_primitives']=len(ct)
    (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
