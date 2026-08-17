#!/usr/bin/env python3
import argparse,csv,gzip,json,pathlib,re,time
from collections import defaultdict
import requests


def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def sid(x): return str(x).strip().removesuffix('.0')
def read_gz(p):
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def walk(o):
    if isinstance(o,dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
def normkey(k): return re.sub(r'[^a-z0-9]','',str(k).lower())
def val(d,names):
    m={normkey(k):v for k,v in d.items()}
    for n in names:
        if normkey(n) in m:
            try:return int(round(float(str(m[normkey(n)]).replace('%',''))))
            except: pass
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir); out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    team_exact=read_gz(pick(cur,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
    player_exact=read_gz(pick(cur,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'))
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    team_targets=[]; player_targets=[]
    for r in reg:
        season=r['season']; game=gid(r['game_id'])
        teams=[sid(x) for x in str(r.get('team_ids') or '').split('|') if x.strip()]
        if int(float(r.get('team_target_count') or 0))>0:
            for t in teams:team_targets.append((season,game,t))
    # Exact player targets are explicitly present when registry has target_player_ids; otherwise infer from current residual blocker file if present.
    for r in reg:
        season=r['season'];game=gid(r['game_id'])
        for p in str(r.get('target_player_ids') or r.get('player_ids') or '').split('|'):
            if p.strip(): player_targets.append((season,game,sid(p)))
    sess=requests.Session();sess.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Accept':'application/json, text/plain, */*','Origin':'https://www.nba.com','Referer':'https://www.nba.com/'})
    qa={'status':'PASS_NO_PROGRESS','lanes':{},'team_targets':len(team_targets),'player_targets_discovered':len(player_targets)}
    promoted_team=[]; promoted_player=[]

    # LANE 1: official NBA Stats team game totals. This is independent of BRef/ESPN and is accepted only after zero-mismatch controls.
    cache={}
    def nba_team(game):
        if game in cache:return cache[game]
        params={'GameID':game,'StartPeriod':0,'EndPeriod':14,'StartRange':0,'EndRange':0,'RangeType':0}
        urls=['https://stats.nba.com/stats/boxscoretraditionalv2','https://stats.nba.com/stats/boxscoretraditionalv3']
        last=None
        for u in urls:
            try:
                rr=sess.get(u,params=params,timeout=18);rr.raise_for_status();js=rr.json(); rows={}
                if 'resultSets' in js:
                    sets=js['resultSets'] if isinstance(js['resultSets'],list) else [js['resultSets']]
                    for rs in sets:
                        headers=rs.get('headers') or []; data=rs.get('rowSet') or []
                        h=[normkey(x) for x in headers]
                        if 'teamid' not in h:continue
                        for row in data:
                            d=dict(zip(headers,row));t=sid(d.get(headers[h.index('teamid')]))
                            o=val(d,['OREB','offensiveRebounds']);de=val(d,['DREB','defensiveRebounds'])
                            if o is not None and de is not None:rows[t]=(o,de)
                else:
                    for d in walk(js):
                        t=val(d,['teamId','TEAM_ID']); o=val(d,['reboundsOffensive','OREB','offensiveRebounds']);de=val(d,['reboundsDefensive','DREB','defensiveRebounds'])
                        if t is not None and o is not None and de is not None:rows[sid(t)]=(o,de)
                if len(rows)>=2:
                    tids=list(rows)
                    ans={t:(rows[t][0],rows[t][1],rows[[q for q in tids if q!=t][0]][0],rows[[q for q in tids if q!=t][0]][1]) for t in tids if len([q for q in tids if q!=t])==1}
                    cache[game]=ans; time.sleep(.08);return ans
            except Exception as e:last=repr(e)
        raise RuntimeError(last or 'NBA_TEAM_NO_ROWS')
    controls=[];mism=[];errors=[];seasons=set()
    by=defaultdict(list)
    for r in team_exact:by[r['season']].append(r)
    for season in sorted(by):
        games=[]
        for r in sorted(by[season],key=lambda z:(gid(z['game_id']),sid(z['team_id']))):
            if gid(r['game_id']) not in games:games.append(gid(r['game_id']))
            if len(games)>=6:break
        for r in by[season]:
            if gid(r['game_id']) not in games:continue
            try:
                got=nba_team(gid(r['game_id'])).get(sid(r['team_id']))
                exp=tuple(int(round(float(r[k]))) for k in ['team_oreb','team_dreb','opponent_oreb','opponent_dreb'])
                controls.append(1);seasons.add(season)
                if got!=exp:mism.append({'season':season,'game_id':gid(r['game_id']),'team_id':sid(r['team_id']),'expected':exp,'got':got})
            except Exception as e:errors.append({'season':season,'game_id':gid(r['game_id']),'error':repr(e)})
    gate=len(controls)>=50 and len(seasons)>=10 and not mism
    terr=[]
    if gate:
        for season,game,t in team_targets:
            try:
                got=nba_team(game).get(t)
                if got is None:raise RuntimeError('team absent')
                promoted_team.append({'season':season,'game_id':game,'team_id':t,'team_oreb':got[0],'team_dreb':got[1],'opponent_oreb':got[2],'opponent_dreb':got[3],'provenance':'NBA Stats official team boxscore; zero-mismatch retained exact control gate'})
            except Exception as e:terr.append({'season':season,'game_id':game,'team_id':t,'error':repr(e)})
    qa['lanes']['nba_official_team_boxscore']={'gate':gate,'controls':len(controls),'seasons':len(seasons),'mismatches':len(mism),'errors':len(errors),'promoted':len(promoted_team),'target_errors':len(terr),'mismatch_examples':mism[:8],'error_examples':errors[:8],'target_error_examples':terr[:8]}

    # LANE 2: PBP Stats per-game Player rows. Promote only if the API exposes team/opponent rebound-on-floor fields and reproduces retained exact player-game primitives with zero mismatches.
    pcache={}
    aliases={
      'seconds_on':['Seconds','SecondsPlayed','SecondsOn','seconds_on'],
      'team_oreb_on':['TeamOffRebounds','TeamOREB','TeamOffensiveRebounds','team_oreb_on'],
      'team_dreb_on':['TeamDefRebounds','TeamDREB','TeamDefensiveRebounds','team_dreb_on'],
      'opponent_oreb_on':['OpponentOffRebounds','OpponentOREB','OpponentOffensiveRebounds','opponent_oreb_on'],
      'opponent_dreb_on':['OpponentDefRebounds','OpponentDREB','OpponentDefensiveRebounds','opponent_dreb_on']}
    def pbp_player(game):
        if game in pcache:return pcache[game]
        rr=sess.get('https://api.pbpstats.com/get-game-stats',params={'GameId':game,'Type':'Player'},timeout=25);rr.raise_for_status();js=rr.json();ans={}
        for d in walk(js):
            pid=None
            for k,v in d.items():
                if normkey(k) in ('playerid','entityid') and re.fullmatch(r'\d+',sid(v)):pid=sid(v);break
            if not pid:continue
            z={name:val(d,names) for name,names in aliases.items()}
            if all(v is not None for v in z.values()):ans[pid]=z
        pcache[game]=ans;time.sleep(.05);return ans
    pctrl=[];pmism=[];perr=[];pseasons=set();schema_samples=[]
    for r in sorted(player_exact,key=lambda z:(z['season'],gid(z['game_id']),sid(z['player_id'])))[:350]:
        try:
            rows=pbp_player(gid(r['game_id']))
            got=rows.get(sid(r['player_id']))
            if not rows and len(schema_samples)<3:schema_samples.append({'game_id':gid(r['game_id']),'note':'no exact field-set discovered'})
            if got is None:raise RuntimeError('NO_EXACT_FIELD_SET_OR_PLAYER')
            exp={k:int(round(float(r[k]))) for k in ['seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on']}
            pctrl.append(1);pseasons.add(r['season'])
            if any(got[k]!=exp[k] for k in exp):pmism.append({'season':r['season'],'game_id':gid(r['game_id']),'player_id':sid(r['player_id']),'expected':exp,'got':got})
        except Exception as e:perr.append({'season':r['season'],'game_id':gid(r['game_id']),'player_id':sid(r['player_id']),'error':repr(e)})
    pgate=len(pctrl)>=100 and len(pseasons)>=10 and not pmism
    qa['lanes']['pbpstats_direct_game_player_exact_fields']={'gate':pgate,'controls':len(pctrl),'seasons':len(pseasons),'mismatches':len(pmism),'errors':len(perr),'promoted':0,'schema_samples':schema_samples,'mismatch_examples':pmism[:8],'error_examples':perr[:8]}
    # Targets are only promotable when explicit ids exist in the registry; otherwise this lane remains diagnostic and fail closed.
    if pgate and player_targets:
        for season,game,p in player_targets:
            try:
                got=pbp_player(game).get(p)
                if got is None:continue
                promoted_player.append({'season':season,'game_id':game,'team_id':'','player_id':p,**got,'provenance':'PBP Stats direct game-player exact fields; zero-mismatch retained exact control gate'})
            except:pass
        qa['lanes']['pbpstats_direct_game_player_exact_fields']['promoted']=len(promoted_player)

    tf=['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']
    with gzip.open(out/'CANDIDATE_TEAM_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=tf);w.writeheader();w.writerows(promoted_team)
    pf=['season','game_id','team_id','player_id','seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on','provenance']
    with gzip.open(out/'CANDIDATE_PLAYER_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=pf);w.writeheader();w.writerows(promoted_player)
    qa['new_team_facts']=len(promoted_team);qa['new_player_facts']=len(promoted_player)
    if promoted_team or promoted_player:qa['status']='PASS_PROGRESS'
    (out/'SUPERVISOR_SOURCE_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps(qa,indent=2))

if __name__=='__main__':main()
