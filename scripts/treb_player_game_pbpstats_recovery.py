#!/usr/bin/env python3
import csv, gzip, json, pathlib, time
from collections import defaultdict
import requests

CUR=pathlib.Path('/tmp/current')
BASE=pathlib.Path('/tmp/shared/base')
GATED=pathlib.Path('/tmp/shared/player_gated'); GATED.mkdir(parents=True,exist_ok=True)
OUT=pathlib.Path('/tmp/out'); OUT.mkdir(parents=True,exist_ok=True)
REG_PATH=next(CUR.rglob('NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'))

S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json','Origin':'https://www.pbpstats.com','Referer':'https://www.pbpstats.com/'})
cache={}

def gid(v): return str(v).strip().removesuffix('.0').zfill(10)
def tid(v): return str(v).strip().removesuffix('.0')
def pid(v): return str(v).strip().removesuffix('.0')
def season_from_gid(g):
    g=gid(g); y=2000+int(g[3:5]); return f'{y}-{str((y+1)%100).zfill(2)}'
def num(r,k):
    v=r.get(k,0); return 0.0 if v in (None,'') else float(v)
def seconds(r):
    if r.get('SecondsPlayed') not in (None,''): return float(r['SecondsPlayed'])
    m=str(r.get('Minutes') or '0').strip()
    if ':' in m:
        a,b=m.split(':',1); return 60*float(a)+float(b)
    return 60*float(m or 0)

def get_stats(game,typ):
    ck=(gid(game),typ)
    if ck in cache:return cache[ck]
    last=None
    for attempt in range(6):
        try:
            r=S.get('https://api.pbpstats.com/get-game-stats',params={'GameId':gid(game),'Type':typ},timeout=30)
            if getattr(r,'status_code',200)==200:
                js=r.json(); cache[ck]=js; time.sleep(.18); return js
            last=RuntimeError(f'HTTP_{getattr(r,"status_code",None)}')
        except Exception as e:
            last=e
        time.sleep(min(6.0, .75*(2**attempt)))
    raise last or RuntimeError('PBP_REQUEST_FAILED')

def side_for(js,team):
    home=tid(js.get('home_team_id'))
    away=tid(js.get('away_team_id'))
    t=tid(team)
    if t==home:return 'Home'
    if t==away:return 'Away'
    raise RuntimeError(f'TEAM_NOT_IN_RESPONSE:{t}:home={home}:away={away}')

def iter_side_rows(js,side):
    obj=(js.get('stats') or {}).get(side)
    if not isinstance(obj,dict): raise RuntimeError(f'NO_{side}_BLOCK')
    for period,rows in obj.items():
        if not str(period).isdigit() or int(period)<1 or not isinstance(rows,list): continue
        for r in rows:
            if isinstance(r,dict): yield r

def lineup_contains(row,player):
    return pid(player) in {pid(x) for x in str(row.get('EntityId') or '').split('-') if x}

def derive(season,game,team,player):
    own=get_stats(game,'Lineup')
    opp=get_stats(game,'LineupOpponent')
    side=side_for(own,team)
    if side_for(opp,team)!=side: raise RuntimeError('SIDE_DISAGREEMENT')
    ownrows=[r for r in iter_side_rows(own,side) if lineup_contains(r,player)]
    oprows=[r for r in iter_side_rows(opp,side) if lineup_contains(r,player)]
    if not ownrows:
        other='Away' if side=='Home' else 'Home'
        other_hits=sum(1 for r in iter_side_rows(own,other) if lineup_contains(r,player))
        raise RuntimeError(f'NO_MATCHING_OWN_LINEUPS:side={side}:other_hits={other_hits}')
    if not oprows:
        other='Away' if side=='Home' else 'Home'
        other_hits=sum(1 for r in iter_side_rows(opp,other) if lineup_contains(r,player))
        raise RuntimeError(f'NO_MATCHING_OPP_LINEUPS:side={side}:other_hits={other_hits}')
    return {
        'seconds_on':sum(seconds(r) for r in ownrows),
        'team_oreb_on':sum(num(r,'OffRebounds') for r in ownrows),
        'team_dreb_on':sum(num(r,'DefRebounds') for r in ownrows),
        'opponent_oreb_on':sum(num(r,'OffRebounds') for r in oprows),
        'opponent_dreb_on':sum(num(r,'DefRebounds') for r in oprows),
    }

# Production targets: only current unresolved player primitives.
targets=[]
with open(REG_PATH,newline='',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        for tp in str(r.get('player_targets') or '').split('|'):
            if not tp.strip(): continue
            team,player=tp.split(':',1)
            targets.append({'season':r['season'],'game_id':gid(r['game_id']),'team_id':tid(team),'player_id':pid(player)})
targets=list({(x['season'],x['game_id'],x['team_id'],x['player_id']):x for x in targets}.values())

# Controls: retained exact player-game primitives; one player per game keeps HTTP load low.
control_roots=[pathlib.Path('/tmp/recovered_old'),pathlib.Path('/tmp/recovered_mid'),pathlib.Path('/tmp/recovered_new'),BASE]
control_files=[]
for root in control_roots:
    if root.exists(): control_files += list(root.rglob('*PLAYER_GAME_PRIMITIVES.csv.gz'))
by=defaultdict(list); seen_control=set()
for p in control_files:
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            need={'game_id','team_id','player_id','seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on'}
            if not need <= set(r): continue
            r['game_id']=gid(r['game_id']); r['team_id']=tid(r['team_id']); r['player_id']=pid(r['player_id']); r['season']=r.get('season') or season_from_gid(r['game_id'])
            q=(r['game_id'],r['team_id'],r['player_id'])
            if q in seen_control: continue
            seen_control.add(q); by[r['season']].append(r)
controls=[]
for s in sorted(by):
    seen_games=set()
    for r in sorted(by[s],key=lambda z:(z['game_id'],z['team_id'],z['player_id'])):
        if r['game_id'] in seen_games: continue
        seen_games.add(r['game_id']); controls.append(r)
        if len(seen_games)>=6: break

fields=['seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on']
evaluated=[]; mism=[]; errors=[]; seasons=set()
for r in controls:
    try:
        got=derive(r['season'],r['game_id'],r['team_id'],r['player_id']); exp={k:float(r[k]) for k in fields}
        reb_ok=all(abs(got[k]-exp[k])<1e-9 for k in fields[1:]); sec_ok=abs(got['seconds_on']-exp['seconds_on'])<=1.01
        rec={'season':r['season'],'game_id':r['game_id'],'team_id':r['team_id'],'player_id':r['player_id'],'expected':exp,'got':got,'rebound_exact':reb_ok,'seconds_within_1s':sec_ok}
        evaluated.append(rec); seasons.add(r['season'])
        if not (reb_ok and sec_ok): mism.append(rec)
    except Exception as e:
        errors.append({'season':r['season'],'game_id':r['game_id'],'team_id':r['team_id'],'player_id':r['player_id'],'error':repr(e)})
    if len(evaluated)>=72: break

gate=len(evaluated)>=50 and len(seasons)>=10 and not mism
promoted=[]; target_errors=[]
if gate:
    for r in targets:
        try:
            got=derive(r['season'],r['game_id'],r['team_id'],r['player_id'])
            promoted.append({**r,**got,'provenance':'pbpstats game-specific exact lineup + lineup-opponent; admitted by >=50 controls >=10 seasons zero rebound mismatches'})
        except Exception as e: target_errors.append({**r,'error':repr(e)})

outp=GATED/'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'; outfields=['season','game_id','team_id','player_id']+fields+['provenance']
with gzip.open(outp,'wt',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=outfields); w.writeheader(); w.writerows(promoted)
qa={'status':'PASS' if gate and promoted else 'FAIL_CLOSED','gate_pass':gate,'control_files':len(control_files),'control_candidates':len(seen_control),'controls_evaluated':len(evaluated),'control_seasons':len(seasons),'control_mismatches':len(mism),'control_errors':len(errors),'targets_requested':len(targets),'targets_promoted':len(promoted),'targets_unresolved':len(targets)-len(promoted),'mismatch_examples':mism[:8],'control_error_examples':errors[:12],'target_error_examples':target_errors[:12],'integrity':{'minimum_controls':50,'minimum_control_seasons':10,'zero_rebound_mismatches_required':True,'seconds_tolerance':1.01,'modeling_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
(OUT/'PLAYER_GAME_PBPSTATS_RECOVERY_QA.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa,indent=2),flush=True)
if gate and promoted: (GATED/'PASS_GATE').write_text(str(len(promoted)))
