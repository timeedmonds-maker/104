#!/usr/bin/env python3
import argparse,csv,io,json,pathlib,tarfile,urllib.request,re
from collections import defaultdict,Counter

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def season_year(s): return s.split('-')[0]
def download_csv(kind,year):
    url=f'https://github.com/shufinskiy/nba_data/raw/main/datasets/{kind}_{year}.tar.xz'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=240) as r:b=r.read()
    with tarfile.open(fileobj=io.BytesIO(b),mode='r:xz') as tf:
        names=[n for n in tf.getnames() if n.lower().endswith('.csv')]
        if not names: raise RuntimeError(f'no csv in {kind} archive')
        fh=tf.extractfile(names[0]); txt=io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline='')
        rd=csv.DictReader(txt); fields=rd.fieldnames or []; rows=list(rd)
    return fields,rows,url

def game_field(fields):
    return next((f for f in fields if f.upper().replace('_','')=='GAMEID'),None)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=defaultdict(set)
    for r in reg:
        if int(float(r.get('team_target_count') or 0))>0 or int(float(r.get('player_target_count') or 0))>0:
            targets[r['season']].add(gid(r['game_id']))
    qa={'status':'PASS','target_seasons':sorted(targets),'seasons':{},'errors':[]}
    idish=re.compile(r'(player|person|team|id)',re.I)
    timeish=re.compile(r'(clock|time|period|event|action|number)',re.I)
    for s in sorted(targets):
        try:
            y=season_year(s); tg=targets[s]
            pf,pr,pu=download_csv('pbpstats',y); nf,nr,nu=download_csv('nbastats',y)
            pg=game_field(pf); ng=game_field(nf)
            if not pg or not ng: raise RuntimeError(f'missing game id field pbp={pg} nba={ng}')
            p=[r for r in pr if gid(r.get(pg,'')) in tg]; n=[r for r in nr if gid(r.get(ng,'')) in tg]
            p_games=set(gid(r.get(pg,'')) for r in p); n_games=set(gid(r.get(ng,'')) for r in n)
            p_candidates=[f for f in pf if idish.search(f) or timeish.search(f)]
            n_candidates=[f for f in nf if idish.search(f) or timeish.search(f)]
            p_nonempty={f:sum(bool(str(r.get(f,'')).strip()) for r in p) for f in pf}
            n_nonempty={f:sum(bool(str(r.get(f,'')).strip()) for r in n) for f in nf}
            # Preserve compact representative payloads. These are diagnostic only and never promoted.
            qa['seasons'][s]={
                'pbpstats_url':pu,'nbastats_url':nu,
                'target_games_requested':len(tg),'pbpstats_games_found':len(p_games),'nbastats_games_found':len(n_games),
                'pbpstats_fields':pf,'nbastats_fields':nf,
                'pbpstats_candidate_identity_time_fields':p_candidates,
                'nbastats_candidate_identity_time_fields':n_candidates,
                'pbpstats_nonempty_counts':p_nonempty,'nbastats_nonempty_counts':n_nonempty,
                'pbpstats_first_rows':p[:8],'nbastats_first_rows':n[:12]
            }
        except Exception as e:
            qa['errors'].append({'season':s,'error':repr(e)})
    if qa['errors']: qa['status']='PARTIAL'
    (out/'STATIC_PAIR_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2))
    compact={}
    for s,v in qa['seasons'].items():
        compact[s]={
            'games':[v['target_games_requested'],v['pbpstats_games_found'],v['nbastats_games_found']],
            'pbp_candidate_fields':v['pbpstats_candidate_identity_time_fields'],
            'nba_candidate_fields':v['nbastats_candidate_identity_time_fields'],
            'pbp_first_row':v['pbpstats_first_rows'][:1],
            'nba_first_two_rows':v['nbastats_first_rows'][:2]
        }
    print(json.dumps({'status':qa['status'],'errors':qa['errors'],'pair_feasibility':compact},indent=2))
if __name__=='__main__':main()
