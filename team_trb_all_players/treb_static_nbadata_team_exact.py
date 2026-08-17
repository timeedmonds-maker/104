#!/usr/bin/env python3
import argparse,csv,gzip,io,json,pathlib,re,tarfile,urllib.request
from collections import defaultdict

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def sid(x): return str(x).strip().removesuffix('.0')
def iv(x): return int(round(float(x)))
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def read_gz(p):
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def season_year(season): return season.split('-')[0]
def download_csv(year):
    url=f'https://github.com/shufinskiy/nba_data/raw/main/datasets/nbastats_{year}.tar.xz'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=240) as r: b=r.read()
    with tarfile.open(fileobj=io.BytesIO(b),mode='r:xz') as tf:
        n=next(n for n in tf.getnames() if n.lower().endswith('.csv'))
        fh=tf.extractfile(n); txt=io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline='')
        return list(csv.DictReader(txt)),url

def parse_team_rebounds(rows, target_games):
    # NBA play-by-play rebound descriptions carry each player's cumulative (Off:x Def:y).
    # Taking each player's final maxima and summing by team reconstructs player-attributed
    # offensive/defensive rebounds. Promotion is allowed only after a large exact control gate.
    mx=defaultdict(lambda:[0,0])
    pat=re.compile(r'REBOUND\s*\(Off:(\d+)\s+Def:(\d+)\)',re.I)
    for r in rows:
        g=gid(r.get('GAME_ID',''))
        if g not in target_games or str(r.get('EVENTMSGTYPE','')).strip()!='4': continue
        desc=(r.get('HOMEDESCRIPTION') or '')+' '+(r.get('VISITORDESCRIPTION') or '')
        m=pat.search(desc)
        if not m: continue
        p=sid(r.get('PLAYER1_ID','')); t=sid(r.get('PLAYER1_TEAM_ID',''))
        if not p or p=='0' or not t: continue
        k=(g,t,p); a=mx[k]; a[0]=max(a[0],int(m.group(1))); a[1]=max(a[1],int(m.group(2)))
    team=defaultdict(lambda:[0,0])
    for (g,t,p),(o,d) in mx.items(): team[(g,t)][0]+=o; team[(g,t)][1]+=d
    games=defaultdict(dict)
    for (g,t),v in team.items():games[g][t]=tuple(v)
    out={}
    for g,ts in games.items():
        if len(ts)!=2: continue
        ids=list(ts)
        out[g]={ids[0]:(*ts[ids[0]],*ts[ids[1]]),ids[1]:(*ts[ids[1]],*ts[ids[0]])}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    exact=read_gz(pick(cur,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=[]
    for r in reg:
        if int(float(r.get('team_target_count') or 0))<=0: continue
        for t in str(r.get('team_ids') or '').split('|'):
            if t.strip():targets.append((r['season'],gid(r['game_id']),sid(t)))
    targets=sorted(set(targets)); target_seasons=sorted(set(s for s,_,_ in targets))
    by=defaultdict(list)
    for r in exact:by[r['season']].append(r)
    # 10 control games per affected season, excluding residual target games.
    target_games={g for _,g,_ in targets}; controls=[]
    for s in target_seasons:
        seen=[]
        for r in sorted(by[s],key=lambda z:(gid(z['game_id']),sid(z['team_id']))):
            g=gid(r['game_id'])
            if g in target_games:continue
            if g not in seen:seen.append(g)
            if len(seen)>=10:break
        controls += [r for r in by[s] if gid(r['game_id']) in seen]
    need=defaultdict(set)
    for r in controls:need[r['season']].add(gid(r['game_id']))
    for s,g,t in targets:need[s].add(g)
    parsed={};urls={};errors=[]
    for s in target_seasons:
        try:
            rows,url=download_csv(season_year(s));urls[s]=url;parsed[s]=parse_team_rebounds(rows,need[s])
        except Exception as e:errors.append({'season':s,'error':repr(e)})
    mism=[];evaluated=0;seasons=set()
    for r in controls:
        s=r['season'];g=gid(r['game_id']);t=sid(r['team_id']);got=(parsed.get(s,{}).get(g) or {}).get(t)
        if got is None:continue
        evaluated+=1;seasons.add(s);exp=tuple(iv(r[k]) for k in ['team_oreb','team_dreb','opponent_oreb','opponent_dreb'])
        if got!=exp:mism.append({'season':s,'game_id':g,'team_id':t,'expected':exp,'got':got})
    gate=evaluated>=150 and len(seasons)>=8 and not mism and not errors
    promoted=[];target_missing=[]
    if gate:
        for s,g,t in targets:
            got=(parsed.get(s,{}).get(g) or {}).get(t)
            if got is None:target_missing.append({'season':s,'game_id':g,'team_id':t});continue
            promoted.append({'season':s,'game_id':g,'team_id':t,'team_oreb':got[0],'team_dreb':got[1],'opponent_oreb':got[2],'opponent_dreb':got[3],'provenance':'shufinskiy/nba_data nbastats static archive; player cumulative rebound counters; gated against retained exact team-game primitives'})
    tf=['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']
    with gzip.open(out/'CANDIDATE_TEAM_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=tf);w.writeheader();w.writerows(promoted)
    pf=['season','game_id','team_id','player_id','seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on','provenance']
    with gzip.open(out/'CANDIDATE_PLAYER_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=pf).writeheader()
    qa={'status':'PASS_PROGRESS' if promoted else 'PASS_NO_PROGRESS','lane':'static_nbadata_player_cumulative_team_rebounds','gate':gate,'controls_evaluated':evaluated,'control_seasons':len(seasons),'control_mismatches':len(mism),'download_errors':errors,'mismatch_examples':mism[:20],'team_targets':len(targets),'promoted_team_facts':len(promoted),'target_missing':target_missing,'source_urls':urls}
    (out/'STATIC_NBADATA_TEAM_QA.json').write_text(json.dumps(qa,indent=2));print(json.dumps(qa,indent=2))
if __name__=='__main__':main()
