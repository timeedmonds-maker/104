#!/usr/bin/env python3
import argparse,csv,json,pathlib,tarfile,re,subprocess,tempfile
from collections import defaultdict,Counter

UPSTREAM_COMMIT='e829d4678be1e075f99e5d41a1c5f97089be446b'
UPSTREAM='https://github.com/shufinskiy/nba_data.git'

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]

def prepare_upstream(root):
    root=pathlib.Path(root)
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'remote','add','origin',UPSTREAM],check=True)
    # Smart-git transport is materially more reliable for these large historical blobs than GitHub's blob REST endpoint.
    subprocess.run(['git','-C',str(root),'-c','http.version=HTTP/1.1','fetch','--depth=1','--no-tags','origin',UPSTREAM_COMMIT],check=True,timeout=600)
    return root

def download(year,wanted,repo_dir,tmpdir):
    archive=pathlib.Path(tmpdir)/f'pbpstats_{year}.tar.xz'
    spec=f'{UPSTREAM_COMMIT}:datasets/pbpstats_{year}.tar.xz'
    with open(archive,'wb') as f:
        subprocess.run(['git','-C',str(repo_dir),'show',spec],stdout=f,check=True,timeout=600)
    if archive.stat().st_size==0: raise RuntimeError(f'empty archive {year}')
    with tarfile.open(archive,mode='r:xz') as tf:
        names=[n for n in tf.getnames() if n.lower().endswith('.csv')]
        if not names: raise RuntimeError('no csv')
        fh=tf.extractfile(names[0])
        if fh is None: raise RuntimeError('csv unreadable')
        import io
        txt=io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline='')
        rd=csv.DictReader(txt); fields=rd.fieldnames or []
        gf=game_field(fields)
        if not gf: raise RuntimeError('no GAMEID field')
        rows=[r for r in rd if gid(r.get(gf,'')) in wanted]
    archive.unlink(missing_ok=True)
    return fields,rows,spec

def game_field(fs): return next((f for f in fs if f.upper().replace('_','')=='GAMEID'),None)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir); out=pathlib.Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=defaultdict(set)
    for r in reg: targets[r['season']].add(gid(r['game_id']))
    sub_re=re.compile(r'\b(SUB|SUBSTITUTION|ENTERS THE GAME|ENTER THE GAME|REPLAC(?:ES|ED)|FOR)\b',re.I)
    period_re=re.compile(r'\b(START OF|END OF|PERIOD|QUARTER|OVERTIME)\b',re.I)
    qa={'status':'PASS','target_games':sum(map(len,targets.values())),'target_seasons':len(targets),'upstream_commit':UPSTREAM_COMMIT,'seasons':{},'errors':[]}
    with tempfile.TemporaryDirectory(prefix='treb_upstream_') as gitdir, tempfile.TemporaryDirectory(prefix='treb_archives_') as tmpdir:
        try: repo=prepare_upstream(gitdir)
        except Exception as e:
            qa['status']='FAIL_TRANSPORT'; qa['errors'].append({'scope':'upstream_fetch','error':repr(e)})
            (out/'STATIC_PBPSTATS_LINEUP_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2)); print(json.dumps(qa,indent=2)); return
        for season in sorted(targets):
            try:
                fields,rows,spec=download(season[:4],targets[season],repo,tmpdir); gf=game_field(fields)
                s={'source':spec,'games':{}}
                for g in sorted(targets[season]):
                    gr=[r for r in rows if gid(r.get(gf,''))==g]
                    subs=[]; periods=[]; first=[]
                    for i,r in enumerate(gr):
                        txt='\n'.join(str(r.get(k,'') or '') for k in ('DESCRIPTION','EVENTS'))
                        rec={'row':i,'period':r.get('PERIOD',''),'starttime':r.get('STARTTIME',''),'team':r.get('TEAM',''),'description':r.get('DESCRIPTION',''),'events':r.get('EVENTS','')}
                        if sub_re.search(txt): subs.append(rec)
                        if period_re.search(txt): periods.append(rec)
                        if len(first)<12 and txt.strip(): first.append(rec)
                    names=Counter(); name_re=re.compile(r"\b([A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+){1,3})\s+(?:MISS|REBOUND|makes|made|foul|FOUL|turnover|TURNOVER|SUB|enters|checks)",re.I)
                    for r in gr:
                        txt='\n'.join(str(r.get(k,'') or '') for k in ('DESCRIPTION','EVENTS'))
                        for m in name_re.finditer(txt): names[m.group(1).strip()]+=1
                    s['games'][g]={'rows':len(gr),'sub_like_count':len(subs),'period_marker_count':len(periods),'sub_examples':subs[:30],'period_examples':periods[:16],'first_event_rows':first,'name_examples':names.most_common(30)}
                qa['seasons'][season]=s
            except Exception as e: qa['errors'].append({'season':season,'error':repr(e)})
    if qa['errors']: qa['status']='PARTIAL'
    allgames=[v for s in qa['seasons'].values() for v in s['games'].values()]
    qa['summary']={'games_examined':len(allgames),'games_with_rows':sum(x['rows']>0 for x in allgames),'games_with_sub_like_events':sum(x['sub_like_count']>0 for x in allgames),'games_with_period_markers':sum(x['period_marker_count']>0 for x in allgames),'total_sub_like_events':sum(x['sub_like_count'] for x in allgames)}
    (out/'STATIC_PBPSTATS_LINEUP_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps({'status':qa['status'],'summary':qa['summary'],'errors':qa['errors']},indent=2))
if __name__=='__main__': main()
