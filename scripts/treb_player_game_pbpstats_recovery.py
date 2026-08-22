#!/usr/bin/env python3
import csv, gzip, json, pathlib, re, time
from collections import defaultdict
import requests

CUR=pathlib.Path('/tmp/current')
BASE=pathlib.Path('/tmp/shared/base')
GATED=pathlib.Path('/tmp/shared/player_gated'); GATED.mkdir(parents=True,exist_ok=True)
OUT=pathlib.Path('/tmp/out'); OUT.mkdir(parents=True,exist_ok=True)
REG_PATH=next(CUR.rglob('NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'))
BASE_PLAYER=next(BASE.rglob('RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'))

S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json','Origin':'https://www.pbpstats.com','Referer':'https://www.pbpstats.com/'})
cache={}; meta_cache={}

def gid(v):
    s=str(v).strip().removesuffix('.0')
    return s.zfill(10)
def tid(v): return str(v).strip().removesuffix('.0')
def pid(v): return str(v).strip().removesuffix('.0')
def season_from_gid(g):
    g=gid(g); yy=int(g[3:5]); y=2000+yy
    return f'{y}-{str((y+1)%100).zfill(2)}'
def num(r,k):
    v=r.get(k,0)
    if v in (None,''): return 0.0
    return float(v)
def seconds(r):
    if r.get('SecondsPlayed') not in (None,''): return float(r['SecondsPlayed'])
    m=str(r.get('Minutes') or '0').strip()
    if ':' in m:
        a,b=m.split(':',1); return 60*float(a)+float(b)
    return 60*float(m or 0)

def game_meta(season):
    if season in meta_cache:return meta_cache[season]
    r=S.get('https://api.pbpstats.com/get-games/nba',params={'Season':season,'SeasonType':'Regular Season'},timeout=30); r.raise_for_status(); js=r.json()
    out={}
    def walk(x):
        if isinstance(x,dict):
            lower={str(k).lower().replace('_',''):k for k in x}
            kg=lower.get('gameid') or lower.get('idgame'); kh=lower.get('hometeamid') or lower.get('teamidhome'); ka=lower.get('awayteamid') or lower.get('visitorteamid') or lower.get('teamidaway')
            if kg and kh and ka:
                out[gid(x[kg])]={'Home':tid(x[kh]),'Away':tid(x[ka])}
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(js)
    # Some historical schedules expose playoffs separately. Merge them fail-closed.
    try:
        r=S.get('https://api.pbpstats.com/get-games/nba',params={'Season':season,'SeasonType':'Playoffs'},timeout=30); r.raise_for_status(); js=r.json(); walk(js)
    except Exception: pass
    meta_cache[season]=out; return out

def get_stats(game,typ):
    ck=(game,typ)
    if ck in cache:return cache[ck]
    r=S.get('https://api.pbpstats.com/get-game-stats',params={'GameId':gid(game),'Type':typ},timeout=30); r.raise_for_status(); js=r.json(); cache[ck]=js; return js

def side_for(season,game,team):
    m=game_meta(season).get(gid(game))
    if not m: raise RuntimeError('NO_GAME_META')
    for side,t in m.items():
        if tid(t)==tid(team): return side
    raise RuntimeError('TEAM_NOT_IN_GAME_META')

def iter_side_rows(js,side):
    obj=js.get(side)
    if not isinstance(obj,dict): raise RuntimeError(f'NO_{side}_BLOCK')
    for period,rows in obj.items():
        # Endpoint is period-split. Only numeric period buckets are additive.
        if not str(period).isdigit() or int(period)<1: continue
        if not isinstance(rows,list): continue
        for r in rows:
            if isinstance(r,dict): yield r

def lineup_contains(row,player):
    ent=str(row.get('EntityId') or '')
    return pid(player) in {pid(x) for x in ent.split('-') if x}

def derive(season,game,team,player):
    side=side_for(season,game,team)
    own=get_stats(game,'Lineup'); opp=get_stats(game,'LineupOpponent')
    ownrows=[r for r in iter_side_rows(own,side) if lineup_contains(r,player)]
    oprows=[r for r in iter_side_rows(opp,side) if lineup_contains(r,player)]
    if not ownrows: raise RuntimeError('NO_MATCHING_OWN_LINEUPS')
    if not oprows: raise RuntimeError('NO_MATCHING_OPP_LINEUPS')
    sec=sum(seconds(r) for r in ownrows)
    # LineupOpponent is opponent production indexed by the same on-floor lineup identity.
    return {
        'seconds_on':sec,
        'team_oreb_on':sum(num(r,'OffRebounds') for r in ownrows),
        'team_dreb_on':sum(num(r,'DefRebounds') for r in ownrows),
        'opponent_oreb_on':sum(num(r,'OffRebounds') for r in oprows),
        'opponent_dreb_on':sum(num(r,'DefRebounds') for r in oprows),
    }

# Current unresolved player targets: exactly the 11-game lane encoded by the authoritative registry.
targets=[]
with open(REG_PATH,newline='',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        for tp in str(r.get('player_targets') or '').split('|'):
            if not tp.strip(): continue
            team,player=tp.split(':',1)
            targets.append({'season':r['season'],'game_id':gid(r['game_id']),'team_id':tid(team),'player_id':pid(player)})
uniq={(x['season'],x['game_id'],x['team_id'],x['player_id']):x for x in targets}; targets=list(uniq.values())

# Retained exact controls. Spread deterministically across seasons to prove the game-specific endpoint.
controls=[]; by=defaultdict(list)
with gzip.open(BASE_PLAYER,'rt',encoding='utf-8',newline='') as f:
    for r in csv.DictReader(f):
        r['game_id']=gid(r['game_id']); r['team_id']=tid(r['team_id']); r['player_id']=pid(r['player_id']); r['season']=r.get('season') or season_from_gid(r['game_id']); by[r['season']].append(r)
for s in sorted(by):
    # distinct games first; one primitive per game is enough for the gate and avoids redundant requests
    seen=set()
    for r in sorted(by[s],key=lambda z:(z['game_id'],z['team_id'],z['player_id'])):
        if r['game_id'] in seen: continue
        seen.add(r['game_id']); controls.append(r)
        if len(seen)>=6: break

fields=['seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on']
evaluated=[]; mism=[]; errors=[]; seasons=set()
for r in controls:
    try:
        got=derive(r['season'],r['game_id'],r['team_id'],r['player_id'])
        exp={k:float(r[k]) for k in fields}
        reb_ok=all(abs(got[k]-exp[k])<1e-9 for k in fields[1:])
        sec_ok=abs(got['seconds_on']-exp['seconds_on'])<=1.01
        rec={'season':r['season'],'game_id':r['game_id'],'team_id':r['team_id'],'player_id':r['player_id'],'expected':exp,'got':got,'rebound_exact':reb_ok,'seconds_within_1s':sec_ok}
        evaluated.append(rec); seasons.add(r['season'])
        if not (reb_ok and sec_ok): mism.append(rec)
    except Exception as e:
        errors.append({'season':r['season'],'game_id':r['game_id'],'team_id':r['team_id'],'player_id':r['player_id'],'error':repr(e)})
    if len(evaluated)>=70: break

gate=len(evaluated)>=50 and len(seasons)>=10 and not mism
promoted=[]; target_errors=[]
if gate:
    for r in targets:
        try:
            got=derive(r['season'],r['game_id'],r['team_id'],r['player_id'])
            promoted.append({**r,**got,'provenance':'pbpstats game-specific exact lineup + lineup-opponent; admitted by >=50 controls >=10 seasons zero rebound mismatches'})
        except Exception as e:
            target_errors.append({**r,'error':repr(e)})

outp=GATED/'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'
outfields=['season','game_id','team_id','player_id']+fields+['provenance']
with gzip.open(outp,'wt',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=outfields); w.writeheader(); w.writerows(promoted)
qa={
  'status':'PASS' if gate and promoted else 'FAIL_CLOSED',
  'gate_pass':gate,
  'controls_evaluated':len(evaluated),'control_seasons':len(seasons),'control_mismatches':len(mism),'control_errors':len(errors),
  'targets_requested':len(targets),'targets_promoted':len(promoted),'targets_unresolved':len(targets)-len(promoted),
  'mismatch_examples':mism[:5],'control_error_examples':errors[:8],'target_error_examples':target_errors[:12],
  'integrity':{'minimum_controls':50,'minimum_control_seasons':10,'zero_rebound_mismatches_required':True,'seconds_tolerance':1.01,'modeling_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}
}
(OUT/'PLAYER_GAME_PBPSTATS_RECOVERY_QA.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa,indent=2),flush=True)
if gate and promoted: (GATED/'PASS_GATE').write_text(str(len(promoted)))
