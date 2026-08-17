#!/usr/bin/env python3
import argparse,csv,gzip,io,json,pathlib,tarfile,urllib.request,re
from collections import Counter,defaultdict

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def read_gz(p):
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def season_year(s): return s.split('-')[0]
def download_csv(year):
    url=f'https://github.com/shufinskiy/nba_data/raw/main/datasets/pbpstats_{year}.tar.xz'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=240) as r:b=r.read()
    with tarfile.open(fileobj=io.BytesIO(b),mode='r:xz') as tf:
        names=[n for n in tf.getnames() if n.lower().endswith('.csv')]
        if not names: raise RuntimeError('no csv in archive')
        fh=tf.extractfile(names[0]); txt=io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline='')
        rd=csv.DictReader(txt); fields=rd.fieldnames or []; rows=list(rd)
    return fields,rows,url

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=defaultdict(set)
    for r in reg:
        if int(float(r.get('team_target_count') or 0))>0 or int(float(r.get('player_target_count') or 0))>0:
            targets[r['season']].add(gid(r['game_id']))
    qa={'status':'PASS','target_seasons':sorted(targets),'seasons':{},'errors':[]}
    id_pat=re.compile(r'\b\d{7,10}\b')
    for s in sorted(targets):
        try:
            fields,rows,url=download_csv(season_year(s)); tg=targets[s]
            gamekey=next((f for f in fields if f.upper() in ('GAMEID','GAME_ID')),None)
            if not gamekey: raise RuntimeError('no game id field')
            rr=[r for r in rows if gid(r.get(gamekey,'')) in tg]
            nonempty={f:sum(bool(str(r.get(f,'')).strip()) for r in rr) for f in fields}
            samples={}
            for f in fields:
                vals=[str(r.get(f,'')) for r in rr if str(r.get(f,'')).strip()]
                if vals:samples[f]=vals[:3]
            events_field=next((f for f in fields if f.upper()=='EVENTS'),None)
            event_ids=[]
            if events_field:
                for r in rr[:200]: event_ids.extend(id_pat.findall(str(r.get(events_field,''))))
            qa['seasons'][s]={
                'url':url,'fields':fields,'field_count':len(fields),'archive_rows':len(rows),
                'target_games_requested':len(tg),'target_games_found':len(set(gid(r.get(gamekey,'')) for r in rr)),
                'target_rows':len(rr),'nonempty_counts':nonempty,'samples':samples,
                'numeric_ids_seen_in_first_200_target_events':Counter(event_ids).most_common(30)
            }
        except Exception as e:qa['errors'].append({'season':s,'error':repr(e)})
    if qa['errors']:qa['status']='PARTIAL'
    (out/'STATIC_PBPSTATS_SCHEMA_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps({'status':qa['status'],'target_seasons':len(targets),'errors':qa['errors'],'field_sets':{s:v['fields'] for s,v in qa['seasons'].items()}},indent=2))
if __name__=='__main__':main()
