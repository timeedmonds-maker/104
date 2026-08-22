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
 bu=f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{g}.json'; pu=f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{g}.json'
 b,be=getjson(bu); p,pe=getjson(pu)
 if b is None or p is None:return None,be or pe
 bg=b.get('game') or b; acts=(p.get('game') or p).get('actions') or []
 base={}; team_total={}
 for side in ('homeTeam','awayTeam'):
  z=bg.get(side) or {}; t=tid(z.get('teamId')); st=z.get('statistics') or {}
  if st.get('reboundsOffensive') is None or st.get('reboundsDefensive') is None:return None,f'missing player rebound totals {g} {t}'
  base[t]=(float(st['reboundsOffensive']),float(st['reboundsDefensive']))
  rt=st.get('reboundsTeam')
  if rt is None:return None,f'missing reboundsTeam {g} {t}'
  team_total[t]=int(rt)
 shots={}
 for a in acts:
  if a.get('isFieldGoal')==1 or str(a.get('actionType','')).lower() in {'2pt','3pt','freethrow'}:
   n=a.get('actionNumber'); team=a.get('teamId')
   if n is not None and team not in (None,'',0,'0'): shots[int(n)]=tid(team)
 split=collections.defaultdict(lambda:[0,0]); unresolved=[]; team_action_count=collections.Counter()
 for a in acts:
  if str(a.get('actionType') or '').lower()!='rebound':continue
  person=a.get('personId');
  try: person=int(person or 0)
  except: person=0
  if person!=0:continue
  team=a.get('teamId')
  if team in (None,'',0,'0'):continue
  team=tid(team); team_action_count[team]+=1
  sn=a.get('shotActionNumber'); shooter=None
  try: shooter=shots.get(int(sn)) if sn is not None else None
  except: shooter=None
  if shooter is None:
   unresolved.append({'actionNumber':a.get('actionNumber'),'team_id':team,'clock':a.get('clock'),'period':a.get('period'),'description':a.get('description'),'shotActionNumber':sn}); continue
  if shooter==team: split[team][0]+=1
  else: split[team][1]+=1
 if set(base)!=set(team_total):return None,f'team mismatch {g}'
 for t in base:
  if split[t][0]+split[t][1] != team_total[t]:
   return None,f'team rebound invariant fail {g} {t}: official={team_total[t]} linked={split[t][0]+split[t][1]} all_team_actions={team_action_count[t]} unresolved={len([x for x in unresolved if x["team_id"]==t])}'
 out={t:(base[t][0]+split[t][0],base[t][1]+split[t][1]) for t in base}
 return {'facts':out,'base':base,'team_total':team_total,'team_split':dict(split),'team_actions':dict(team_action_count),'unresolved':unresolved},''
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--current',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)
 reg=read(one(a.current,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv')); shared=read(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')); game_season={gid(r['game_id']):str(r['season']) for r in reg}
 targets=set()
 for r in reg:
  if int(str(r['season']).split('-')[0])<2021:continue
  g=gid(r['game_id'])
  for x in str(r.get('team_ids') or '').split('|'):
   if x.strip() and x.strip().lower()!='nan':targets.add((g,tid(x)))
 exact={}
 for r in shared:
  g=gid(r['game_id']); t=tid(r['team_id']); s=str(r.get('season') or game_season.get(g,'')); exact[(g,t)]={'season':s,'team_oreb':float(r['team_oreb']),'team_dreb':float(r['team_dreb']),'opponent_oreb':float(r['opponent_oreb']),'opponent_dreb':float(r['opponent_dreb'])}
 controls=[k for k,v in exact.items() if int(str(v['season']).split('-')[0])>=2021 and k not in targets]
 games=sorted({g for g,t in controls}|{g for g,t in targets}); fetched={}; failures=[]; evidence=[]
 for g in games:
  z,err=gamefacts(g)
  if z is None:failures.append({'game_id':g,'season':game_season.get(g,''),'error':err});continue
  fetched[g]=z['facts']
  for t,(o,dv) in z['facts'].items(): evidence.append({'game_id':g,'season':game_season.get(g,''),'team_id':t,'team_oreb':o,'team_dreb':dv,'official_team_rebounds':z['team_total'][t],'linked_team_oreb':z['team_split'].get(t,[0,0])[0],'linked_team_dreb':z['team_split'].get(t,[0,0])[1]})
 checked=[]; mism=[]; seasons=set()
 for g,t in controls:
  if g not in fetched or t not in fetched[g]:continue
  opp=[x for x in fetched[g] if x!=t]
  if len(opp)!=1:continue
  o,dv=fetched[g][t]; oo,od=fetched[g][opp[0]]; e=exact[(g,t)]; z={'game_id':g,'season':e['season'],'team_id':t,'expected_team_oreb':e['team_oreb'],'expected_team_dreb':e['team_dreb'],'expected_opponent_oreb':e['opponent_oreb'],'expected_opponent_dreb':e['opponent_dreb'],'cdn_team_oreb':o,'cdn_team_dreb':dv,'cdn_opponent_oreb':oo,'cdn_opponent_dreb':od}; checked.append(z); seasons.add(e['season'])
  if any(abs(z[x]-z[y])>1e-9 for x,y in [('cdn_team_oreb','expected_team_oreb'),('cdn_team_dreb','expected_team_dreb'),('cdn_opponent_oreb','expected_opponent_oreb'),('cdn_opponent_dreb','expected_opponent_dreb')]):mism.append(z)
 gate=(len(mism)==0 and len(checked)>=12 and len(seasons)>=2)
 recovered=[]
 if gate:
  for g,t in sorted(targets):
   if (g,t) in exact or g not in fetched or t not in fetched[g]:continue
   opp=[x for x in fetched[g] if x!=t]
   if len(opp)!=1:continue
   o,dv=fetched[g][t]; oo,od=fetched[g][opp[0]]; recovered.append({'season':game_season[g],'game_id':g,'team_id':t,'team_oreb':o,'team_dreb':dv,'opponent_oreb':oo,'opponent_dreb':od,'provenance':'official NBA liveData box player rebounds + official reboundsTeam split by rebound shotActionNumber linked miss; exact team-rebound count invariant; zero-mismatch controls'})
 def write(name,rs,fields=None):
  if fields is None:fields=sorted({k for r in rs for k in r}) if rs else []
  with open(OUT/name,'w',newline='',encoding='utf-8') as f:
   if fields:w=csv.DictWriter(f,fieldnames=list(fields));w.writeheader();w.writerows(rs)
 write('CDN_LINKED_TEAM_FACTS.csv',recovered,['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']);write('CDN_LINKED_CONTROLS.csv',checked);write('CDN_LINKED_CONTROL_MISMATCHES.csv',mism);write('CDN_LINKED_FAILURES.csv',failures);write('CDN_LINKED_EVIDENCE.csv',evidence)
 qa={'status':'PASS' if gate and recovered else 'FAIL_CLOSED','source':'official NBA liveData box + shot-linked PBP team rebounds','target_team_facts':len(targets),'control_comparisons':len(checked),'control_seasons':sorted(seasons),'control_mismatches':len(mism),'source_failures':len(failures),'promoted_target_team_facts':len(recovered),'targets_promoted':len(recovered),'integrity':{'official_reboundsTeam_count_required':True,'all_team_rebounds_uniquely_shot_linked_required':True,'zero_control_mismatch_required':True,'raw_exact_counts_only':True,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
 (OUT/'TEAM_FACT_SOURCE_RACE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n');print(json.dumps(qa,indent=2,sort_keys=True))
 if not gate or not recovered:raise SystemExit(2)
if __name__=='__main__':main()
