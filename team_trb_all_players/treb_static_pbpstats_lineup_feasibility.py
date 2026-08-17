#!/usr/bin/env python3
import argparse,csv,io,json,pathlib,tarfile,urllib.request,re
from collections import defaultdict,Counter

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def download(year):
    url=f'https://github.com/shufinskiy/nba_data/raw/main/datasets/pbpstats_{year}.tar.xz'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=240) as r:b=r.read()
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
                # event text name census: capitalized multi-token names preceding common actions.
                names=Counter()
                name_re=re.compile(r'\b([A-Z][A-Za-z.\'\-]+(?: [A-Z][A-Za-z.\'\-]+){1,3})\s+(?:MISS|REBOUND|makes|made|foul|FOUL|turnover|TURNOVER|SUB|enters|checks)',re.I)
                for r in gr:
                    txt='\n'.join(str(r.get(k,'') or '') for k in ('DESCRIPTION','EVENTS'))
                    for m in name_re.finditer(txt): names[m.group(1).strip()]+=1
                s['games'][g]={'rows':len(gr),'sub_like_count':len(subs),'period_marker_count':len(periods),'sub_examples':subs[:30],'period_examples':periods[:16],'first_event_rows':first,'name_examples':names.most_common(30)}
            qa['seasons'][season]=s
        except Exception as e: qa['errors'].append({'season':season,'error':repr(e)})
    if qa['errors']: qa['status']='PARTIAL'
    # Summary is deliberately fail-closed: no claim of reconstructability without explicit substitutions.
    allgames=[v for s in qa['seasons'].values() for v in s['games'].values()]
    qa['summary']={'games_examined':len(allgames),'games_with_sub_like_events':sum(x['sub_like_count']>0 for x in allgames),'games_with_period_markers':sum(x['period_marker_count']>0 for x in allgames),'total_sub_like_events':sum(x['sub_like_count'] for x in allgames)}
    (out/'STATIC_PBPSTATS_LINEUP_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps({'status':qa['status'],'summary':qa['summary'],'errors':qa['errors']},indent=2))
if __name__=='__main__': main()
