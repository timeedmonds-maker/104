#!/usr/bin/env python3
import csv, gzip, json, re, time, pathlib
from collections import defaultdict
import requests
from bs4 import BeautifulSoup

CUR=pathlib.Path('/tmp/current')
OUT=pathlib.Path('/tmp/shared/gated'); OUT.mkdir(parents=True,exist_ok=True)
QA=pathlib.Path('/tmp/out/TEAM_FACT_SOURCE_RACE_QA.json')

def pick(root,name):
    xs=list(root.rglob(name))
    if not xs: raise RuntimeError('missing '+name)
    return xs[0]
def rows_gz(p):
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def tid(x): return str(x).strip().removesuffix('.0')

def tup(r): return tuple(int(round(float(r[k]))) for k in ['team_oreb','team_dreb','opponent_oreb','opponent_dreb'])

exact=rows_gz(pick(CUR,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
reg=list(csv.DictReader(open(pick(CUR,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
targets=[]
for r in reg:
    if int(float(r.get('team_target_count') or 0))<=0: continue
    for t in str(r.get('team_ids') or '').split('|'):
        if t.strip(): targets.append({'season':r['season'],'game_id':gid(r['game_id']),'team_id':tid(t)})

# Deterministic controls: up to eight retained exact rows per season. This yields >50 rows across >10 seasons.
by=defaultdict(list)
for r in exact: by[r['season']].append(r)
controls=[]
for season in sorted(by):
    for r in sorted(by[season],key=lambda x:(gid(x['game_id']),tid(x['team_id'])))[:8]: controls.append(r)

S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept':'application/json,text/html,*/*','Referer':'https://www.nba.com/','Origin':'https://www.nba.com'})
cache={}

def finish_team_pairs(rows):
    z={}
    for t,o,d in rows: z[str(t)]=(int(o),int(d))
    if len(z)!=2:return None
    ans={}
    for t,(o,d) in z.items():
        oth=[v for k,v in z.items() if k!=t]
        if len(oth)!=1:return None
        ans[t]=(o,d,oth[0][0],oth[0][1])
    return ans

def nba_stats(game):
    ck=('nba_stats',game)
    if ck in cache:return cache[ck]
    u='https://stats.nba.com/stats/boxscoretraditionalv3'
    p={'GameID':game,'StartPeriod':0,'EndPeriod':0,'StartRange':0,'EndRange':0,'RangeType':0}
    r=S.get(u,params=p,timeout=20); r.raise_for_status(); js=r.json()
    rows=[]
    def walk(x):
        if isinstance(x,dict):
            t=x.get('teamId') or x.get('teamID')
            st=x.get('statistics') if isinstance(x.get('statistics'),dict) else x
            if t and isinstance(st,dict):
                o=st.get('reboundsOffensive'); d=st.get('reboundsDefensive')
                if o is not None and d is not None: rows.append((t,o,d))
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(js)
    # de-duplicate identical team rows
    uniq={str(t):(t,o,d) for t,o,d in rows}
    ans=finish_team_pairs(uniq.values()); cache[ck]=ans; return ans

def nba_cdn(game):
    ck=('nba_cdn',game)
    if ck in cache:return cache[ck]
    u=f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game}.json'
    r=S.get(u,timeout=20); r.raise_for_status(); js=r.json().get('game',{})
    rows=[]
    for side in ('homeTeam','awayTeam'):
        tr=js.get(side) or {}; st=tr.get('statistics') or {}
        if tr.get('teamId') and st.get('reboundsOffensive') is not None and st.get('reboundsDefensive') is not None:
            rows.append((tr['teamId'],st['reboundsOffensive'],st['reboundsDefensive']))
    ans=finish_team_pairs(rows); cache[ck]=ans; return ans

# pbpstats game metadata is already a proven reachable metadata lane in this project.
meta_cache={}
def game_meta(season):
    if season in meta_cache:return meta_cache[season]
    r=S.get('https://api.pbpstats.com/get-games/nba',params={'Season':season,'SeasonType':'Regular Season'},timeout=30); r.raise_for_status(); js=r.json()
    out={}
    def walk(x):
        if isinstance(x,dict):
            lower={str(k).lower().replace('_',''):k for k in x}
            kg=lower.get('gameid') or lower.get('idgame'); kd=lower.get('date') or lower.get('gamedate') or lower.get('gamedateest') or lower.get('dateest')
            kh=lower.get('hometeamid') or lower.get('teamidhome'); ka=lower.get('awayteamid') or lower.get('visitorteamid') or lower.get('teamidaway')
            if kg and kd and kh and ka:
                g=gid(x[kg]); m=re.search(r'(20\d\d)[-/]?(\d\d)[-/]?(\d\d)',str(x[kd]))
                if m: out[g]=(m.group(1)+m.group(2)+m.group(3),tid(x[kh]),tid(x[ka]))
            for v in x.values():walk(v)
        elif isinstance(x,list):
            for v in x:walk(v)
    walk(js); meta_cache[season]=out; return out

BASE_CODES={'1610612737':'ATL','1610612738':'BOS','1610612739':'CLE','1610612741':'CHI','1610612742':'DAL','1610612743':'DEN','1610612744':'GSW','1610612745':'HOU','1610612746':'LAC','1610612747':'LAL','1610612748':'MIA','1610612749':'MIL','1610612750':'MIN','1610612752':'NYK','1610612753':'ORL','1610612754':'IND','1610612755':'PHI','1610612756':'PHO','1610612757':'POR','1610612758':'SAC','1610612759':'SAS','1610612761':'TOR','1610612762':'UTA','1610612763':'MEM','1610612764':'WAS','1610612765':'DET'}
def bref_code(team,season):
    if team=='1610612751': return 'NJN' if int(season[:4])<=2011 else 'BRK'
    if team=='1610612760': return 'SEA' if int(season[:4])<=2007 else 'OKC'
    if team=='1610612740':
        y=int(season[:4]); return 'NOK' if y in (2005,2006) else ('NOH' if y<=2012 else 'NOP')
    if team=='1610612766': return 'CHH' if int(season[:4])<=2001 else ('CHA' if int(season[:4])<=2013 else 'CHO')
    return BASE_CODES.get(team)

def code_to_tid(code,season):
    for t in list(BASE_CODES)+['1610612751','1610612760','1610612740','1610612766']:
        if bref_code(t,season)==code:return t
    return None

def bref(season,game):
    ck=('bref',game)
    if ck in cache:return cache[ck]
    meta=game_meta(season).get(game)
    if not meta: raise RuntimeError('NO_META')
    date,home,away=meta; hc=bref_code(home,season)
    if not hc: raise RuntimeError('NO_BREF_HOME_CODE')
    u=f'https://www.basketball-reference.com/boxscores/{date}0{hc}.html'
    r=S.get(u,timeout=30); r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser'); rows=[]
    # Team total footer in each basic game table.
    for table in soup.find_all('table',id=re.compile(r'^box-.*-game-basic$')):
        m=re.match(r'^box-(.*)-game-basic$',table.get('id','')); code=m.group(1) if m else ''
        t=code_to_tid(code,season)
        foot=table.find('tfoot')
        if not t or not foot: continue
        tr=foot.find('tr'); vals={td.get('data-stat'):td.get_text(strip=True) for td in tr.find_all(['th','td'])}
        if vals.get('orb') not in (None,'') and vals.get('drb') not in (None,''): rows.append((t,int(vals['orb']),int(vals['drb'])))
    ans=finish_team_pairs(rows); cache[ck]=ans; time.sleep(1.2); return ans

sources=[('nba_stats',lambda s,g:nba_stats(g)),('bref',bref),('nba_cdn',lambda s,g:nba_cdn(g))]
reports={}; admitted=[]
for name,fn in sources:
    evaluated=[]; mism=[]; errors=[]; seasons=set()
    for r in controls:
        g=gid(r['game_id']); t=tid(r['team_id'])
        try:
            got=fn(r['season'],g)
            if not got or t not in got: raise RuntimeError('NO_TEAM_FACT')
            rec={'season':r['season'],'game_id':g,'team_id':t,'expected':tup(r),'got':got[t]}
            evaluated.append(rec);seasons.add(r['season'])
            if tuple(got[t])!=tup(r):mism.append(rec)
        except Exception as e: errors.append({'season':r['season'],'game_id':g,'team_id':t,'error':repr(e)})
    gate=len(evaluated)>=50 and len(seasons)>=10 and not mism
    reports[name]={'gate_pass':gate,'controls_evaluated':len(evaluated),'control_seasons':len(seasons),'mismatches':len(mism),'errors':len(errors),'mismatch_examples':mism[:5],'error_examples':errors[:8]}
    print(name,json.dumps(reports[name]))
    if gate:admitted.append((name,fn))
    # A fully admitted source is enough; continue CDN only as optional target corroboration is not required.

promoted=[]; target_errors=[]; conflicts=[]
for r in targets:
    vals=[]
    for name,fn in admitted:
        try:
            got=fn(r['season'],r['game_id'])
            if got and r['team_id'] in got: vals.append((name,tuple(got[r['team_id']])))
        except Exception as e: target_errors.append({**r,'source':name,'error':repr(e)})
    uniq={v for _,v in vals}
    if len(uniq)>1:
        conflicts.append({**r,'values':[(n,list(v)) for n,v in vals]}); continue
    if len(uniq)==1:
        v=next(iter(uniq)); promoted.append({**r,'team_oreb':v[0],'team_dreb':v[1],'opponent_oreb':v[2],'opponent_dreb':v[3],'provenance':'exact boxscore; admitted zero-mismatch source(s): '+','.join(n for n,_ in vals)})

fields=['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']
with gzip.open(OUT/'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(promoted)
qa={'status':'PASS' if admitted and not conflicts else 'FAIL_CLOSED','admitted_sources':[n for n,_ in admitted],'source_reports':reports,'target_team_facts_requested':len(targets),'target_team_facts_promoted':len(promoted),'target_team_facts_unresolved':len(targets)-len(promoted),'target_conflicts':len(conflicts),'target_errors':len(target_errors),'conflict_examples':conflicts[:5],'target_error_examples':target_errors[:10],'integrity':{'minimum_controls':50,'minimum_control_seasons':10,'zero_mismatches_required':True,'modeling_used':False,'opponent_inference_used':False,'rounded_backsolve_used':False}}
QA.write_text(json.dumps(qa,indent=2));print(json.dumps(qa,indent=2))
if admitted and promoted and not conflicts:(OUT/'PASS_GATE').write_text(str(len(promoted)))
