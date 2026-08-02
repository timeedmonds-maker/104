import csv, json, os, random, re, time, hashlib
from pathlib import Path
from urllib.parse import urlencode
import requests

START, END, MIN_MIN = 2000, 2025, 10000
OUT=Path('team_trb_all_players/output'); CACHE=Path('team_trb_all_players/cache')
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
HEAD={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Referer':'https://www.nba.com/','Origin':'https://www.nba.com','Accept':'application/json, text/plain, */*','x-nba-stats-origin':'stats','x-nba-stats-token':'true'}
BASE='https://stats.nba.com/stats'; JINA='https://r.jina.ai/http://stats.nba.com/stats'
TEAM_IDS=[1610612737,1610612738,1610612739,1610612740,1610612741,1610612742,1610612743,1610612744,1610612745,1610612746,1610612747,1610612748,1610612749,1610612750,1610612751,1610612752,1610612753,1610612754,1610612755,1610612756,1610612757,1610612758,1610612759,1610612760,1610612761,1610612762,1610612763,1610612764,1610612765,1610612766]
S=requests.Session(); S.headers.update(HEAD)

def season(y): return f'{y}-{str(y+1)[-2:]}'
def pct(x):
    x=float(x); return x/100 if x>1.5 else x

def params(seas,tid,measure):
    return {'DateFrom':'','DateTo':'','GameSegment':'','LastNGames':0,'LeagueID':'00','Location':'','MeasureType':measure,'Month':0,'OpponentTeamID':0,'Outcome':'','PORound':0,'PaceAdjust':'N','PerMode':'Totals','Period':0,'PlusMinus':'N','Rank':'N','Season':seas,'SeasonSegment':'','SeasonType':'Regular Season','ShotClockRange':'','TeamID':tid,'VsConference':'','VsDivision':''}

def sets(payload):
    x=payload.get('resultSets',payload.get('resultSet'))
    if isinstance(x,dict): x=[x]
    if not isinstance(x,list): raise ValueError('No result sets')
    return x

def named(payload,name):
    for rs in sets(payload):
        if rs.get('name')==name:
            h=rs['headers']; return [dict(zip(h,r)) for r in rs.get('rowSet',[])]
    raise KeyError(f'{name}; available={[r.get("name") for r in sets(payload)]}')

def get(endpoint,p):
    key=hashlib.sha256(json.dumps([endpoint,sorted(p.items())]).encode()).hexdigest()[:24]
    f=CACHE/f'{endpoint}_{key}.json'
    if f.exists(): return json.loads(f.read_text())
    errs=[]
    for a in range(5):
        try:
            r=S.get(f'{BASE}/{endpoint}',params=p,timeout=60); r.raise_for_status(); data=r.json(); sets(data)
            f.write_text(json.dumps(data)); time.sleep(.65+random.random()*.3); return data
        except Exception as e:
            errs.append(str(e)); time.sleep(min(2**a,16)+random.random())
    try:
        r=requests.get(f'{JINA}/{endpoint}?{urlencode(p)}',headers={'User-Agent':HEAD['User-Agent']},timeout=120); r.raise_for_status(); t=r.text.strip()
        try: data=json.loads(t)
        except Exception:
            m=re.search(r'(\{.*"resultSets".*\})',t,re.S)
            if not m: raise ValueError('No NBA JSON in relay')
            data=json.loads(m.group(1))
        sets(data); f.write_text(json.dumps(data)); return data
    except Exception as e: raise RuntimeError('; '.join(errs+[str(e)]))

def active_teams(seas):
    p={'Conference':'','DateFrom':'','DateTo':'','Division':'','GameScope':'','GameSegment':'','LastNGames':0,'LeagueID':'00','Location':'','MeasureType':'Base','Month':0,'OpponentTeamID':0,'Outcome':'','PORound':0,'PaceAdjust':'N','PerMode':'Totals','Period':0,'PlayerExperience':'','PlayerPosition':'','PlusMinus':'N','Rank':'N','Season':seas,'SeasonSegment':'','SeasonType':'Regular Season','ShotClockRange':'','StarterBench':'','TeamID':0,'VsConference':'','VsDivision':'','Weight':0}
    try:
        rows=named(get('leaguedashteamstats',p),'LeagueDashTeamStats'); ids=sorted({int(r['TEAM_ID']) for r in rows})
        return ids if len(ids)>=29 else TEAM_IDS
    except Exception: return TEAM_IDS

def write(path,rows):
    rows=list(rows)
    if not rows:return
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

detail=[]; failures=[]
for y in range(START,END+1):
    seas=season(y); tids=active_teams(seas); print(seas,len(tids),flush=True)
    for i,tid in enumerate(tids,1):
        try:
            adv=named(get('teamplayeronoffdetails',params(seas,tid,'Advanced')),'PlayersOnCourtTeamPlayerOnOffDetails')
            bas=named(get('teamplayeronoffdetails',params(seas,tid,'Base')),'PlayersOnCourtTeamPlayerOnOffDetails')
            bm={int(r['VS_PLAYER_ID']):r for r in bas}
            for a in adv:
                pid=int(a['VS_PLAYER_ID']); b=bm[pid]
                rp=pct(a.get('REB_PCT',a.get('TEAM_REB_PCT'))); reb=float(b['REB']); mins=float(a.get('MIN') or b.get('MIN') or 0)
                if not .35<=rp<=.70: raise ValueError(f'bad REB_PCT {rp}')
                detail.append({'player_id':pid,'player':a['VS_PLAYER_NAME'],'season':seas,'season_end':y+1,'team_id':int(a['TEAM_ID']),'team':a['TEAM_ABBREVIATION'],'games':float(a.get('GP') or b.get('GP') or 0),'minutes':mins,'team_oreb':float(b.get('OREB') or 0),'team_dreb':float(b.get('DREB') or 0),'team_reb':reb,'team_oreb_pct':round(100*pct(a['OREB_PCT']),3) if a.get('OREB_PCT') not in ('',None) else '', 'team_dreb_pct':round(100*pct(a['DREB_PCT']),3) if a.get('DREB_PCT') not in ('',None) else '', 'team_trb_pct':round(100*rp,3),'rebound_opportunities':reb/rp,'source':f'{BASE}/teamplayeronoffdetails?{urlencode(params(seas,tid,"Advanced"))}'})
            write(OUT/'detail_checkpoint.csv',detail)
        except Exception as e:
            failures.append({'season':seas,'team_id':tid,'error':repr(e)}); print('FAIL',seas,tid,e,flush=True)

detail.sort(key=lambda r:(r['season_end'],r['team_id'],r['player']))
# Deduplicate exact player-season-team rows and fail on conflicting duplicates.
seen={}; clean=[]
for r in detail:
    k=(r['player_id'],r['season'],r['team_id'])
    if k in seen:
        if seen[k]!=r: failures.append({'season':r['season'],'team_id':r['team_id'],'error':f'conflicting duplicate {k}'})
    else: seen[k]=r; clean.append(r)
detail=clean
write(OUT/'player_team_season_detail.csv',detail)

g={}
for r in detail:
    x=g.setdefault(r['player_id'],{'player':r['player'],'minutes':0,'reb':0,'opp':0,'seasons':set(),'stints':0})
    x['minutes']+=r['minutes']; x['reb']+=r['team_reb']; x['opp']+=r['rebound_opportunities']; x['seasons'].add(r['season']); x['stints']+=1
board=[]
for pid,x in g.items():
    if x['minutes']>=MIN_MIN:
        board.append({'rank':0,'player':x['player'],'player_id':pid,'minutes':round(x['minutes'],1),'career_team_trb_pct':round(100*x['reb']/x['opp'],3),'team_rebounds':round(x['reb'],1),'rebound_opportunities':round(x['opp'],3),'seasons_included':len(x['seasons']),'first_season':min(x['seasons']),'last_season':max(x['seasons']),'team_season_stints':x['stints']})
board.sort(key=lambda r:(-r['career_team_trb_pct'],-r['minutes'],r['player']))
for i,r in enumerate(board,1):r['rank']=i
write(OUT/'career_team_trb_leaderboard.csv',board)
if failures: write(OUT/'request_failures.csv',failures)
meta={'start_season':'2000-01','end_season':'2025-26','minimum_minutes':MIN_MIN,'detail_rows':len(detail),'qualifying_players':len(board),'failed_team_seasons':len(failures),'method':'sum(team rebounds) / sum(team rebounds / displayed team REB_PCT)','source':'NBA Stats TeamPlayerOnOffDetails'}
(OUT/'metadata.json').write_text(json.dumps(meta,indent=2)); print(json.dumps(meta,indent=2))
if failures: raise SystemExit(2)
