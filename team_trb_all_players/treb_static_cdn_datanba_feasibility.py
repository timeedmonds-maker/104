#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,pathlib,subprocess,tarfile,tempfile,time
import pandas as pd
UPSTREAM_COMMIT='e829d4678be1e075f99e5d41a1c5f97089be446b'
UPSTREAM='https://github.com/shufinskiy/nba_data.git'
KINDS=('cdnnba','datanba')
def gid(x): return int(float(str(x).strip()))
def pick(root,name):
    h=list(pathlib.Path(root).rglob(name))
    if not h: raise FileNotFoundError(name)
    return h[0]
def prep(root):
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'remote','add','origin',UPSTREAM],check=True)
    for attempt in range(1,5):
        try:
            subprocess.run(['git','-C',str(root),'-c','http.version=HTTP/1.1','fetch','--filter=blob:none','--depth=1','--no-tags','origin',UPSTREAM_COMMIT],check=True,timeout=240)
            return
        except Exception:
            if attempt==4: raise
            time.sleep(attempt*8)
def archive(repo,tmp,kind,year):
    p=tmp/f'{kind}_{year}.tar.xz';spec=f'{UPSTREAM_COMMIT}:datasets/{kind}_{year}.tar.xz'
    with p.open('wb') as f:
        subprocess.run(['git','-C',str(repo),'-c','http.version=HTTP/1.1','show',spec],stdout=f,check=True,timeout=300)
    with tarfile.open(p,'r:xz') as tf:
        ms=[m for m in tf.getmembers() if m.isfile() and m.name.lower().endswith('.csv')]
        if not ms: raise RuntimeError('no csv')
        return pd.read_csv(tf.extractfile(ms[0]),low_memory=False),spec
def game_col(df):
    for c in df.columns:
        if c.upper().replace('_','')=='GAMEID': return c
    return None
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    reg=pd.read_csv(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),low_memory=False)
    targets={}
    for r in reg.to_dict('records'):
        s=str(r['season']); targets.setdefault(s,set()).add(gid(r['game_id']))
    results=[]
    with tempfile.TemporaryDirectory(prefix='treb_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_arc_') as td:
        repo=pathlib.Path(gd);tmp=pathlib.Path(td);prep(repo)
        for season,games in sorted(targets.items()):
            year=season[:4]
            for kind in KINDS:
                row={'season':season,'kind':kind,'target_games':len(games),'games_present':0,'present_game_ids':[],'status':'PASS'}
                try:
                    df,spec=archive(repo,tmp,kind,year);row['source']=spec
                    gc=game_col(df)
                    if not gc: raise RuntimeError('no game id column')
                    nums=pd.to_numeric(df[gc],errors='coerce')
                    d=df[nums.isin(games)].copy();pg=sorted({gid(x) for x in d[gc].dropna().unique()})
                    row['games_present']=len(pg);row['present_game_ids']=pg;row['columns']=list(map(str,df.columns))
                    if kind=='cdnnba' and len(d):
                        ac=next((c for c in d.columns if c.lower()=='actiontype'),None)
                        st=next((c for c in d.columns if c.lower()=='subtype'),None)
                        row['action_type_counts']=(d[ac].astype(str).value_counts().head(30).to_dict() if ac else {})
                        row['sub_type_counts']=(d[st].astype(str).value_counts().head(30).to_dict() if st else {})
                        if ac:
                            low=d[ac].astype(str).str.lower();row['substitution_rows']=int(low.str.contains('sub').sum());row['rebound_rows']=int(low.str.contains('rebound').sum())
                    if kind=='datanba' and len(d):
                        ec=next((c for c in d.columns if c.lower()=='etype'),None)
                        row['etype_counts']=(d[ec].astype(str).value_counts().head(30).to_dict() if ec else {})
                except Exception as e:
                    row['status']='SOURCE_GAP_OR_ERROR';row['error']=f'{type(e).__name__}: {e}'
                results.append(row)
    qa={'status':'PASS','upstream_commit':UPSTREAM_COMMIT,'production_registry_rows':int(len(reg)),'target_seasons':len(targets),'results':results,'integrity':{'diagnostic_only':True,'promotion_performed':False,'exact_materiality_gates_unchanged':True}}
    (out/'STATIC_CDN_DATANBA_FEASIBILITY.json').write_text(json.dumps(qa,indent=2)+'\n')
    print(json.dumps(qa,indent=2),flush=True)
if __name__=='__main__': main()
