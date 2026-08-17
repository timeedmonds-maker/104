#!/usr/bin/env python3
import argparse,base64,csv,io,json,pathlib,tarfile,urllib.request,re
from collections import defaultdict,Counter

PBP_BLOBS={
'2004':'48032e7937b036c3e82d33d5997e2def2ec17a7d',
'2009':'84c719190e81a70814b9d640826cb3477d32da7f',
'2015':'e8a817eb42b94c5bc320a795b66e4c53f8abc90a',
'2017':'9c9de2041451f55cba730ab28a84cab28370e315',
'2018':'9efe1b6b8c612fd4cd50879e43eec7fd242c1bb9',
'2019':'60fc8b6fc262af8d7d58454339e896f86e559c40',
'2020':'e958697e80866cfbb4529e427ae7b706e4180392',
'2021':'38bc8ad744a1014c38e578e190b7af3673e7c35f',
'2022':'f8df65d90b9450db4d435d663269e715acb126ed',
'2023':'93d2308cae153eae6ba6f1724e6e871a1453dca2',
'2024':'ca961ed0f900c079aa034dd22004f481f79a6faa',
}

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def download(year):
    sha=PBP_BLOBS.get(year)
    if not sha: raise RuntimeError(f'no pinned PBPStats blob for {year}')
    url=f'https://api.github.com/repos/shufinskiy/nba_data/git/blobs/{sha}'
    req=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'TREB-exact-recovery'})
    with urllib.request.urlopen(req,timeout=240) as r: obj=json.load(r)
    if obj.get('encoding')!='base64' or not obj.get('content'):
        raise RuntimeError(f'unexpected blob response encoding for {year}: {obj.get("encoding")}')
    b=base64.b64decode(obj['content'])
    with tarfile.open(fileobj=io.BytesIO(b),mode='r:xz') as tf:
        names=[n for n in tf.getnames() if n.lower().endswith('.csv')]
        if not names: raise RuntimeError('no csv')
        txt=io.TextIOWrapper(tf.extractfile(names[0]),encoding='utf-8-sig',errors='replace',newline='')
        rd=csv.DictReader(txt); return rd.fieldnames or [],list(rd),url

def game_field(fs): return next((f for f in fs if f.upper().replace('_','')=='GAMEID'),None)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir); out=pathlib.Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=defaultdict(set)
    for r in reg: targets[r['season']].add(gid(r['game_id']))
    sub_re=re.compile(r'\b(SUB|SUBSTITUTION|ENTERS THE GAME|ENTER THE GAME|REPLAC(?:ES|ED)|FOR)\b',re.I)
    period_re=re.compile(r'\b(START OF|END OF|PERIOD|QUARTER|OVERTIME)\b',re.I)
    qa={'status':'PASS','target_games':sum(map(len,targets.values())),'target_seasons':len(targets),'seasons':{},'errors':[]}
    for season in sorted(targets):
        try:
            fields,rows,url=download(season[:4]); gf=game_field(fields)
            if not gf: raise RuntimeError('no GAMEID field')
            s={'url':url,'games':{}}
            for g in sorted(targets[season]):
                gr=[r for r in rows if gid(r.get(gf,''))==g]
                subs=[]; periods=[]; first=[]
                for i,r in enumerate(gr):
                    txt='\n'.join(str(r.get(k,'') or '') for k in ('DESCRIPTION','EVENTS'))
                    rec={'row':i,'period':r.get('PERIOD',''),'starttime':r.get('STARTTIME',''),'team':r.get('TEAM',''),'description':r.get('DESCRIPTION',''),'events':r.get('EVENTS','')}
                    if sub_re.search(txt): subs.append(rec)
                    if period_re.search(txt): periods.append(rec)
                    if len(first)<12 and txt.strip(): first.append(rec)
                names=Counter()
                name_re=re.compile(r'\b([A-Z][A-Za-z.\'\-]+(?: [A-Z][A-Za-z.\'\-]+){1,3})\s+(?:MISS|REBOUND|makes|made|foul|FOUL|turnover|TURNOVER|SUB|enters|checks)',re.I)
                for r in gr:
                    txt='\n'.join(str(r.get(k,'') or '') for k in ('DESCRIPTION','EVENTS'))
                    for m in name_re.finditer(txt): names[m.group(1).strip()]+=1
                s['games'][g]={'rows':len(gr),'sub_like_count':len(subs),'period_marker_count':len(periods),'sub_examples':subs[:30],'period_examples':periods[:16],'first_event_rows':first,'name_examples':names.most_common(30)}
            qa['seasons'][season]=s
        except Exception as e: qa['errors'].append({'season':season,'error':repr(e)})
    if qa['errors']: qa['status']='PARTIAL'
    allgames=[v for s in qa['seasons'].values() for v in s['games'].values()]
    qa['summary']={'games_examined':len(allgames),'games_with_sub_like_events':sum(x['sub_like_count']>0 for x in allgames),'games_with_period_markers':sum(x['period_marker_count']>0 for x in allgames),'total_sub_like_events':sum(x['sub_like_count'] for x in allgames)}
    (out/'STATIC_PBPSTATS_LINEUP_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps({'status':qa['status'],'summary':qa['summary'],'errors':qa['errors']},indent=2))
if __name__=='__main__': main()
