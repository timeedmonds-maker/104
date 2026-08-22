import argparse,csv,gzip,json,pathlib,time,urllib.request,collections

def gid(x):
 s=str(x).strip().removesuffix('.0')
 try:return str(int(float(s))).zfill(10)
 except:return s.zfill(10)
def tid(x): return str(int(float(x)))
def read(p):
 p=pathlib.Path(p)
 if p.suffix=='.gz':
  with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
 with open(p,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def one(root,pat):
 m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]
def getjson(url):
 h={'User-Agent':'Mozilla/5.0 (compatible; TREB exact recovery/1.0)','Referer':'https://www.nba.com/','Accept':'application/json'}; err=''
 for a in range(5):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=20) as r:return json.load(r),''
  except Exception as e: err=f'{type(e).__name__}: {e}'; time.sleep(2*(a+1))
 return None,err

def gamefacts(g):
 b,be=getjson(f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{g}.json')
 p,pe=getjson(f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{g}.json')
 if b is None or p is None:return None,be or pe
 bg=b.get('game') or b; acts=(p.get('game') or p).get('actions') or []
 base={}
 for side in ('homeTeam','awayTeam'):
  z=bg.get(side) or {}; t=tid(z.get('teamId')); st=z.get('statistics') or {}
  if st.get('reboundsOffensive') is None or st.get('reboundsDefensive') is None:return None,f'missing base rebound totals {g} {t}'
  base[t]=(int(st['reboundsOffensive']),int(st['reboundsDefensive']))
 # Official liveData explicitly labels rebound subtype.  Only generic rebound actions
 # whose subtype is offensive or defensive are countable TREB events.  Other generic
 # rebound actions (e.g. administrative/team/dead-ball records) are excluded.
 team=collections.defaultdict(lambda:[0,0]); evidence=[]
 for a in acts:
  if str(a.get('actionType') or '').lower()!='rebound':continue
  try: person=int(a.get('personId') or 0)
  except: person=0
  if person!=0:continue
  t=a.get('teamId')
  if t in (None,'',0,'0'):continue
  t=tid(t); sub=str(a.get('subType') or '').strip().lower()
  counted=False
  if sub=='offensive': team[t][0]+=1; counted=True
  elif sub=='defensive': team[t][1]+=1; counted=True
  evidence.append({'game_id':g,'team_id':t,'actionNumber':a.get('actionNumber'),'period':a.get('period'),'clock':a.get('clock'),'subType':sub,'description':a.get('description'),'counted':int(counted),'reboundTotal':a.get('reboundTotal'),'reboundOffensiveTotal':a.get('reboundOffensiveTotal'),'reboundDefensiveTotal':a.get('reboundDefensiveTotal')})
 out={t:(base[t][0]+team[t][0],base[t][1]+team[t][1]) for t in base}
 return {'facts':out,'base':base,'team_split':dict(team),'evidence':evidence},''

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--current',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();OUT=pathlib.Path(a.out);OUT.mkdir(parents=True,exist_ok=True)
 reg=read(one(a.current,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv')); shared=read(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
 game_season={gid(r['game_id']):str(r['season']) for r in reg}; targets=set()
 for r in reg:
  g=gid(r['game_id'])
  for x in str(r.get('team_ids') or '').split('|'):
   if x.strip() and x.strip().lower()!='nan':targets.add((g,tid(x)))
 exact={}
 for r in shared:
  g=gid(r['game_id']);t=tid(r['team_id']);s=str(r.get('season') or game_season.get(g,'')); exact[(g,t)]={'season':s,'team_oreb':float(r['team_oreb']),'team_dreb':float(r['team_dreb']),'opponent_oreb':float(r['opponent_oreb']),'opponent_dreb':float(r['opponent_dreb'])}
 target_seasons={game_season[g] for g,t in targets if g in game_season}
 # Broad exact controls from the same affected seasons, capped only to keep wall-clock bounded.
 controls=[]
 for s in sorted(target_seasons):
  ks=[k for k,v in exact.items() if v['season']==s and k not in targets]
  controls.extend(sorted(ks)[:20])
 games=sorted({g for g,t in controls}|{g for g,t in targets}); fetched={}; failures=[]; evidence=[]
 for g in games:
  z,err=gamefacts(g)
  if z is None: failures.append({'game_id':g,'season':game_season.get(g,''),'error':err}); continue
  fetched[g]=z['facts']; evidence.extend(z['evidence'])
 checked=[];mism=[];seasons=set()
 for g,t in controls:
  if g not in fetched or t not in fetched[g]:continue
  opp=[x for x in fetched[g] if x!=t]
  if len(opp)!=1:continue
  o,d=fetched[g][t];oo,od=fetched[g][opp[0]];e=exact[(g,t)]
  q={'game_id':g,'season':e['season'],'team_id':t,'expected_team_oreb':e['team_oreb'],'expected_team_dreb':e['team_dreb'],'expected_opponent_oreb':e['opponent_oreb'],'expected_opponent_dreb':e['opponent_dreb'],'source_team_oreb':o,'source_team_dreb':d,'source_opponent_oreb':oo,'source_opponent_dreb':od};checked.append(q);seasons.add(e['season'])
  if any(abs(q[x]-q[y])>1e-9 for x,y in [('source_team_oreb','expected_team_oreb'),('source_team_dreb','expected_team_dreb'),('source_opponent_oreb','expected_opponent_oreb'),('source_opponent_dreb','expected_opponent_dreb')]):mism.append(q)
 # Require zero mismatches and meaningful cross-season control evidence. Source failures may leave older targets unavailable but cannot weaken controls.
 gate=(len(mism)==0 and len(checked)>=20 and len(seasons)>=2)
 recovered=[]
 if gate:
  for g,t in sorted(targets):
   if (g,t) in exact or g not in fetched or t not in fetched[g]:continue
   opp=[x for x in fetched[g] if x!=t]
   if len(opp)!=1:continue
   o,d=fetched[g][t];oo,od=fetched[g][opp[0]]
   recovered.append({'season':game_season[g],'game_id':g,'team_id':t,'team_oreb':o,'team_dreb':d,'opponent_oreb':oo,'opponent_dreb':od,'provenance':'official NBA liveData team box player rebound totals plus generic PBP rebound actions explicitly subtyped offensive/defensive; zero-mismatch exact controls'})
 def write(n,rs,fields=None):
  if fields is None:fields=sorted({k for r in rs for k in r}) if rs else []
  with open(OUT/n,'w',newline='',encoding='utf-8') as f:
   if fields:w=csv.DictWriter(f,fieldnames=list(fields));w.writeheader();w.writerows(rs)
 write('CDN_SUBTYPE_TEAM_FACTS.csv',recovered,['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']);write('CDN_SUBTYPE_CONTROLS.csv',checked);write('CDN_SUBTYPE_CONTROL_MISMATCHES.csv',mism);write('CDN_SUBTYPE_FAILURES.csv',failures);write('CDN_SUBTYPE_EVENT_EVIDENCE.csv',evidence)
 qa={'status':'PASS' if gate and recovered else 'FAIL_CLOSED','target_team_facts':len(targets),'control_comparisons':len(checked),'control_seasons':sorted(seasons),'control_mismatches':len(mism),'source_failures':len(failures),'promoted_target_team_facts':len(recovered),'integrity':{'official_rebound_subtype_only':True,'zero_control_mismatch_required':True,'raw_exact_counts_only':True,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
 (OUT/'TEAM_FACT_SOURCE_RACE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n');print(json.dumps(qa,indent=2,sort_keys=True))
 if not gate or not recovered:raise SystemExit(2)
if __name__=='__main__':main()
