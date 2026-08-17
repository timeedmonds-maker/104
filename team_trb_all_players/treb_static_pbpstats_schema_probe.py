#!/usr/bin/env python3
import argparse,csv,gzip,io,json,pathlib,tarfile,urllib.request,re,ast
from collections import Counter,defaultdict

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
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

def parse_obj(v):
    if not isinstance(v,str): return v
    s=v.strip()
    if not s:return None
    for fn in (json.loads,ast.literal_eval):
        try:return fn(s)
        except Exception:pass
    return None

def shape(x,depth=0):
    if depth>3:return type(x).__name__
    if isinstance(x,dict):return {str(k):shape(v,depth+1) for k,v in list(x.items())[:30]}
    if isinstance(x,list):return [shape(v,depth+1) for v in x[:5]]
    return type(x).__name__

def walk_keys(x,c):
    if isinstance(x,dict):
        for k,v in x.items():c[str(k)]+=1;walk_keys(v,c)
    elif isinstance(x,list):
        for v in x:walk_keys(v,c)

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
                if vals:samples[f]=vals[:5]
            events_field=next((f for f in fields if f.upper()=='EVENTS'),None)
            event_ids=[]; parsed_shapes=[]; key_counts=Counter(); parse_success=0
            if events_field:
                for r in rr[:500]:
                    raw=str(r.get(events_field,'')); event_ids.extend(id_pat.findall(raw))
                    obj=parse_obj(raw)
                    if obj is not None:
                        parse_success+=1;walk_keys(obj,key_counts)
                        if len(parsed_shapes)<10:parsed_shapes.append(shape(obj))
            qa['seasons'][s]={
                'url':url,'fields':fields,'field_count':len(fields),'archive_rows':len(rows),
                'target_games_requested':len(tg),'target_games_found':len(set(gid(r.get(gamekey,'')) for r in rr)),
                'target_rows':len(rr),'nonempty_counts':nonempty,'samples':samples,
                'first_10_target_rows':rr[:10],
                'events_parse_success_first_500':parse_success,
                'events_shapes':parsed_shapes,
                'events_nested_keys':key_counts.most_common(100),
                'numeric_ids_seen_in_first_500_target_events':Counter(event_ids).most_common(100)
            }
        except Exception as e:qa['errors'].append({'season':s,'error':repr(e)})
    if qa['errors']:qa['status']='PARTIAL'
    (out/'STATIC_PBPSTATS_SCHEMA_QA.json').write_text(json.dumps(qa,indent=2))
    compact={s:{'fields':v['fields'],'parse_success':v['events_parse_success_first_500'],'nested_keys':v['events_nested_keys'][:30],'ids':v['numeric_ids_seen_in_first_500_target_events'][:30]} for s,v in qa['seasons'].items()}
    print(json.dumps({'status':qa['status'],'target_seasons':len(targets),'errors':qa['errors'],'deep':compact},indent=2))
if __name__=='__main__':main()
