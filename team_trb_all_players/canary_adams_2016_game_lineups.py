#!/usr/bin/env python3
"""Decisive game-lineup canary for Steven Adams, OKC 2016-17.

Fetches PBP Stats Type=Lineup and Type=LineupOpponent once per OKC regular-
season game.  The canary fails closed unless the two views expose exactly the
same OKC five-man EntityId keys.  It then aggregates only the lineups containing
Steven Adams and compares exact seconds/rebound counts with the already-locked
PBP reconstruction regression.
"""
from __future__ import annotations

import argparse,gzip,json,time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API='https://api.pbpstats.com/get-game-stats'
SEASON='2016-17'
TEAM_ID=1610612760
PLAYER_ID='203500'
LOCKED={
 'seconds_on':143368,
 'team_oreb_on':816,
 'team_dreb_on':1846,
 'team_reb_on':2662,
 'opp_oreb_on':643,
 'opp_dreb_on':1632,
 'opp_reb_on':2275,
}

def parse_minutes(v:Any)->int:
    s=str(v or '0:00').strip()
    if ':' in s:
        m,sec=s.split(':',1);return int(m)*60+int(round(float(sec)))
    return int(round(float(s)*60))

def entity_has(entity:Any,pid:str)->bool:
    return pid in str(entity or '').split('-')

def session()->requests.Session:
    s=requests.Session()
    retry=Retry(total=6,connect=6,read=6,status=6,backoff_factor=1.2,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])
    s.mount('https://',HTTPAdapter(max_retries=retry));return s

def fetch(s:requests.Session,gid:int,typ:str)->dict:
    game_id=f'{gid:010d}'
    r=s.get(API,params={'GameId':game_id,'Type':typ},timeout=90)
    r.raise_for_status();d=r.json()
    if 'stats' not in d:raise RuntimeError(f'no stats game={game_id} type={typ}: {list(d) if isinstance(d,dict) else type(d)}')
    return d

def side_for(d:dict)->str:
    if int(d['home_team_id'])==TEAM_ID:return 'Home'
    if int(d['away_team_id'])==TEAM_ID:return 'Away'
    raise RuntimeError(f'OKC absent from API game home={d.get("home_team_id")} away={d.get("away_team_id")}')

def row_map(d:dict,side:str)->dict[str,dict]:
    rows=d['stats'][side]['FullGame']
    out={}
    for r in rows:
        eid=str(r.get('EntityId',''))
        if not eid:raise RuntimeError('lineup row missing EntityId')
        if eid in out:raise RuntimeError(f'duplicate EntityId {eid}')
        out[eid]=r
    return out

def n(r:dict,k:str)->int:
    v=r.get(k,0)
    if v in (None,''):return 0
    return int(round(float(v)))

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--schedule',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--cache',type=Path,required=True);a=ap.parse_args()
    with gzip.open(a.schedule,'rt',encoding='utf-8') as f:sched=json.load(f)
    games=[]
    for g in sched['results']:
        if TEAM_ID in (int(g['HomeTeamId']),int(g['AwayTeamId'])):
            games.append({'game_id':int(g['GameId']),'date':str(g['Date'])})
    games.sort(key=lambda x:(x['date'],x['game_id']))
    if len(games)!=82:raise RuntimeError(f'expected 82 OKC games, got {len(games)}')

    sess=session();tot={k:0 for k in LOCKED};off={'seconds_off':0,'team_oreb_off':0,'team_dreb_off':0,'team_reb_off':0,'opp_oreb_off':0,'opp_dreb_off':0,'opp_reb_off':0}
    audit=[];cache=[]
    for i,g in enumerate(games,1):
        gid=g['game_id'];line=fetch(sess,gid,'Lineup');opp=fetch(sess,gid,'LineupOpponent')
        side=side_for(line)
        if side_for(opp)!=side:raise RuntimeError(f'side mismatch game={gid}')
        lm=row_map(line,side);om=row_map(opp,side)
        missing_opp=sorted(set(lm)-set(om));extra_opp=sorted(set(om)-set(lm))
        if missing_opp or extra_opp:
            raise RuntimeError(f'LineupOpponent EntityId mismatch game={gid} missing={missing_opp[:10]} extra={extra_opp[:10]}')
        game_on={k:0 for k in LOCKED};game_off={k:0 for k in off};compact=[]
        for eid,r in lm.items():
            o=om[eid];sec=parse_minutes(r.get('Minutes'))
            if parse_minutes(o.get('Minutes'))!=sec:raise RuntimeError(f'lineup/opponent minute mismatch game={gid} lineup={eid}')
            vals={'seconds':sec,'team_oreb':n(r,'OffRebounds'),'team_dreb':n(r,'DefRebounds'),'team_reb':n(r,'Rebounds'),'opp_oreb':n(o,'OffRebounds'),'opp_dreb':n(o,'DefRebounds'),'opp_reb':n(o,'Rebounds')}
            on=entity_has(eid,PLAYER_ID)
            dest=game_on if on else game_off
            suffix='_on' if on else '_off'
            for base,val in vals.items():dest[base+suffix]=dest.get(base+suffix,0)+val
            compact.append({'EntityId':eid,'Minutes':r.get('Minutes'),'on':on,**vals})
        for k,v in game_on.items():tot[k]+=v
        for k,v in game_off.items():off[k]+=v
        audit.append({'game_id':gid,'date':g['date'],'side':side,'lineups':len(lm),'on_lineups':sum(entity_has(x,PLAYER_ID) for x in lm),'seconds_on':game_on['seconds_on'],'seconds_off':game_off['seconds_off']})
        cache.append({'game_id':gid,'date':g['date'],'side':side,'lineups':compact})
        print(f'ADAMS_GAME {i}/82 {gid} on_seconds={game_on["seconds_on"]} lineups={len(lm)}',flush=True)
        time.sleep(0.15)
    diffs={k:int(tot[k]-LOCKED[k]) for k in LOCKED}
    payload={'status':'PASS' if all(v==0 for v in diffs.values()) else 'FAIL','season':SEASON,'team_id':TEAM_ID,'player_id':PLAYER_ID,'games':len(games),'lineup_opponent_key_alignment':'EXACT_ALL_GAMES','locked':LOCKED,'game_lineup_aggregate_on':tot,'game_lineup_aggregate_off':off,'diff_vs_locked':diffs,'game_audit':audit,'acceptance':'Exact match required for all seven locked Adams TREB regression quantities; no tolerance in this canary.'}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2)+'\n')
    a.cache.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(a.cache,'wt',encoding='utf-8') as f:json.dump({'source':API,'season':SEASON,'team_id':TEAM_ID,'player_id':PLAYER_ID,'games':cache},f,separators=(',',':'))
    print(json.dumps({k:v for k,v in payload.items() if k!='game_audit'},indent=2))
    return 0 if payload['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
