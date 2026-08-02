import csv,json,glob
from pathlib import Path
OUT=Path('team_trb_all_players/final_output'); OUT.mkdir(parents=True,exist_ok=True)

def read(pattern):
 rows=[]
 for f in glob.glob(pattern,recursive=True):
  with open(f,encoding='utf-8-sig',newline='') as h: rows.extend(csv.DictReader(h))
 return rows

def write(path,rows):
 rows=list(rows)
 with open(path,'w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

d=read('season_artifacts/**/player_team_season_detail.csv')
if not d: raise SystemExit('No detail rows downloaded')
seen={}
for r in d:
 k=(int(r['player_id']),r['season'],int(r['team_id']))
 if k in seen and seen[k]!=r: raise SystemExit(f'Conflicting duplicate {k}')
 seen[k]=r
d=list(seen.values()); d.sort(key=lambda r:(int(r['season_end']),int(r['team_id']),r['player']))
write(OUT/'player_team_season_detail.csv',d)
fail=read('season_artifacts/**/request_failures.csv')
if fail:
 write(OUT/'request_failures.csv',fail)
 raise SystemExit(f'{len(fail)} failed team-seasons')
g={}
for r in d:
 pid=int(r['player_id']); x=g.setdefault(pid,{'player':r['player'],'minutes':0.,'reb':0.,'opp':0.,'seasons':set(),'stints':0})
 x['minutes']+=float(r['minutes']); x['reb']+=float(r['team_reb']); x['opp']+=float(r['rebound_opportunities']); x['seasons'].add(r['season']); x['stints']+=1
b=[]
for pid,x in g.items():
 if x['minutes']>=10000:
  b.append({'rank':0,'player':x['player'],'player_id':pid,'minutes':round(x['minutes'],1),'career_team_trb_pct':round(100*x['reb']/x['opp'],3),'team_rebounds':round(x['reb'],1),'rebound_opportunities':round(x['opp'],3),'seasons_included':len(x['seasons']),'first_season':min(x['seasons']),'last_season':max(x['seasons']),'team_season_stints':x['stints']})
b.sort(key=lambda r:(-r['career_team_trb_pct'],-r['minutes'],r['player']))
for i,r in enumerate(b,1):r['rank']=i
write(OUT/'career_team_trb_leaderboard.csv',b)
meta={'start_season':'2000-01','end_season':'2025-26','minimum_minutes':10000,'detail_rows':len(d),'qualifying_players':len(b),'failed_team_seasons':0,'method':'sum(team rebounds) / sum(team rebounds / displayed team REB_PCT)','source':'NBA Stats TeamPlayerOnOffDetails'}
(OUT/'metadata.json').write_text(json.dumps(meta,indent=2)); print(json.dumps(meta,indent=2))
