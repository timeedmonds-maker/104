import csv, hashlib, json, os, re, time
from pathlib import Path
from urllib.parse import urlencode
import requests

START, END, MIN_MIN = 2000, 2025, 10000
OUT = Path('team_trb_all_players/output')
CACHE = Path('team_trb_all_players/cache')
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
TEAM_IDS = list(range(1610612737, 1610612767))
BASE = 'https://stats.nba.com/stats'
RELAY = 'https://r.jina.ai/http://stats.nba.com/stats'
HEAD = {'User-Agent':'Mozilla/5.0','Referer':'https://www.nba.com/','Origin':'https://www.nba.com','Accept':'application/json,text/plain,*/*'}
SMOKE = MIN_MIN == 0


def season(y): return f'{y}-{str(y+1)[-2:]}'
def pct(v):
    v=float(v)
    return v/100 if v>1.5 else v

def params(seas, tid, measure):
    return {'DateFrom':'','DateTo':'','GameSegment':'','LastNGames':0,'LeagueID':'00','Location':'','MeasureType':measure,'Month':0,'OpponentTeamID':0,'Outcome':'','PORound':0,'PaceAdjust':'N','PerMode':'Totals','Period':0,'PlusMinus':'N','Rank':'N','Season':seas,'SeasonSegment':'','SeasonType':'Regular Season','ShotClockRange':'','TeamID':tid,'VsConference':'','VsDivision':''}

def result_sets(data):
    rs=data.get('resultSets', data.get('resultSet'))
    if isinstance(rs, dict): rs=[rs]
    if not isinstance(rs, list): raise ValueError('No result sets')
    return rs

def rows(data, name):
    for rs in result_sets(data):
        if rs.get('name') == name:
            h=rs['headers']
            return [dict(zip(h,r)) for r in rs.get('rowSet',[])]
    raise KeyError(f'{name} not found')

def decode(text):
    text=text.strip().lstrip('\ufeff')
    try: return json.loads(text)
    except Exception:
        m=re.search(r'(\{.*?"resultSets".*\})', text, re.S)
        if not m: raise ValueError(f'No JSON payload; prefix={text[:180]!r}')
        return json.loads(m.group(1))

def get(endpoint, p):
    key=hashlib.sha256(json.dumps([endpoint,sorted(p.items())]).encode()).hexdigest()[:24]
    f=CACHE/f'{endpoint}_{key}.json'
    if f.exists(): return json.loads(f.read_text())
    q=urlencode(p)
    attempts=[('relay', f'{RELAY}/{endpoint}?{q}', 25, {'User-Agent':'Mozilla/5.0'}),('direct', f'{BASE}/{endpoint}', 10, HEAD)]
    errors=[]
    for label,url,timeout,headers in attempts:
        try:
            if label=='direct': r=requests.get(url,params=p,headers=headers,timeout=timeout)
            else: r=requests.get(url,headers=headers,timeout=timeout)
            r.raise_for_status()
            data=decode(r.text)
            result_sets(data)
            f.write_text(json.dumps(data))
            print('SOURCE',label,endpoint,p.get('Season'),p.get('TeamID'),flush=True)
            time.sleep(.15)
            return data
        except Exception as e:
            errors.append(f'{label}: {type(e).__name__}: {e}')
    raise RuntimeError(' | '.join(errors))

def write(path, items):
    items=list(items)
    if not items: return
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(items[0]))
        w.writeheader(); w.writerows(items)

def active_teams(seas):
    if SMOKE: return [1610612737]
    return TEAM_IDS

detail=[]; failures=[]
for y in range(START,END+1):
    seas=season(y)
    tids=active_teams(seas)
    print('SEASON',seas,'TEAMS',len(tids),flush=True)
    for tid in tids:
        try:
            adv=rows(get('teamplayeronoffdetails',params(seas,tid,'Advanced')),'PlayersOnCourtTeamPlayerOnOffDetails')
            bas=rows(get('teamplayeronoffdetails',params(seas,tid,'Base')),'PlayersOnCourtTeamPlayerOnOffDetails')
            bm={int(r['VS_PLAYER_ID']):r for r in bas}
            if not adv or not bm: raise ValueError('empty on/off rows')
            for a in adv:
                pid=int(a['VS_PLAYER_ID']); b=bm.get(pid)
                if not b: continue
                rp=pct(a.get('REB_PCT',a.get('TEAM_REB_PCT')))
                reb=float(b['REB']); mins=float(a.get('MIN') or b.get('MIN') or 0)
                if not .35 <= rp <= .70: raise ValueError(f'bad REB_PCT {rp}')
                detail.append({'player_id':pid,'player':a['VS_PLAYER_NAME'],'season':seas,'season_end':y+1,'team_id':int(a['TEAM_ID']),'team':a['TEAM_ABBREVIATION'],'minutes':mins,'team_reb':reb,'team_trb_pct':round(100*rp,3),'rebound_opportunities':reb/rp})
            write(OUT/'detail_checkpoint.csv',detail)
            print('DONE',seas,tid,len(adv),flush=True)
        except Exception as e:
            failures.append({'season':seas,'team_id':tid,'error':repr(e)})
            print('FAIL',seas,tid,repr(e),flush=True)
            if SMOKE: break

seen={}
for r in detail: seen[(r['player_id'],r['season'],r['team_id'])]=r
detail=sorted(seen.values(),key=lambda r:(r['season_end'],r['team_id'],r['player']))
write(OUT/'player_team_season_detail.csv',detail)
g={}
for r in detail:
    x=g.setdefault(r['player_id'],{'player':r['player'],'minutes':0.0,'reb':0.0,'opp':0.0,'seasons':set(),'stints':0})
    x['minutes']+=r['minutes']; x['reb']+=r['team_reb']; x['opp']+=r['rebound_opportunities']; x['seasons'].add(r['season']); x['stints']+=1
board=[]
for pid,x in g.items():
    if x['minutes']>=MIN_MIN and x['opp']>0:
        board.append({'rank':0,'player':x['player'],'player_id':pid,'minutes':round(x['minutes'],1),'career_team_trb_pct':round(100*x['reb']/x['opp'],3),'seasons_included':len(x['seasons']),'first_season':min(x['seasons']),'last_season':max(x['seasons']),'team_season_stints':x['stints']})
board.sort(key=lambda r:(-r['career_team_trb_pct'],-r['minutes'],r['player']))
for i,r in enumerate(board,1): r['rank']=i
write(OUT/'career_team_trb_leaderboard.csv',board)
if failures: write(OUT/'request_failures.csv',failures)
meta={'start_season':season(START),'end_season':season(END),'minimum_minutes':MIN_MIN,'detail_rows':len(detail),'qualifying_players':len(board),'failed_team_seasons':len(failures),'smoke':SMOKE,'method':'sum(team rebounds) / sum(team rebounds / displayed team REB_PCT)','source':'NBA Stats TeamPlayerOnOffDetails via bounded relay/direct requests'}
(OUT/'metadata.json').write_text(json.dumps(meta,indent=2))
print(json.dumps(meta,indent=2),flush=True)
if failures: raise SystemExit(2)
