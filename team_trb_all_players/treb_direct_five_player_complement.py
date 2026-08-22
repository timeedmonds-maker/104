import argparse,csv,gzip,json,pathlib,collections
REQP=('seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on')
REQT=('team_oreb','team_dreb','opponent_oreb','opponent_dreb')
def pid(x): return str(x).strip().removesuffix('.0')
def tid(x): return str(int(float(x)))
def gid(x):
 s=str(x).strip().removesuffix('.0')
 try:return str(int(float(s))).zfill(10)
 except:return s.zfill(10)
def one(root,pat):
 m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]
def read(p):
 p=pathlib.Path(p)
 if p.suffix=='.gz':
  with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
 with open(p,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def main():
 ap=argparse.ArgumentParser()
 for x in ['current','consensus','pbp','old','mid','newer','game','currentcons','out']: ap.add_argument('--'+x,required=True)
 a=ap.parse_args(); out=pathlib.Path(a.out); out.mkdir(parents=True,exist_ok=True)
 facts=collections.defaultdict(dict); team=collections.defaultdict(dict)
 def putp(k,z,v):
  v=float(v); old=facts[k].get(z)
  if old is not None and abs(old-v)>1e-9: raise SystemExit(f'PLAYER_CONFLICT {k} {z} {old} {v}')
  facts[k][z]=v
 def putt(k,z,v):
  v=float(v); old=team[k].get(z)
  if old is not None and abs(old-v)>1e-9: raise SystemExit(f'TEAM_CONFLICT {k} {z} {old} {v}')
  team[k][z]=v
 for r in read(one(a.current,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz')):
  k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
  for z in REQP: putp(k,z,r[z])
 for r in read(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')):
  k=(gid(r['game_id']),tid(r['team_id']))
  for z in REQT: putt(k,z,r[z])
 for r in read(one(a.consensus,'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv')):
  k=(gid(r['game_id']),tid(r['team_id']),pid(r.get('player_id','')))
  if r['field'] in REQP: putp(k,r['field'],r['value'])
  elif not k[2] and r['field'] in REQT: putt((k[0],k[1]),r['field'],r['value'])
 for root,pat in [(a.old,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz'),(a.mid,'RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz'),(a.newer,'RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz')]:
  for r in read(one(root,pat)):
   k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
   for z in REQP: putp(k,z,r[z])
 for root,pat in [(a.game,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv'),(a.currentcons,'RECOVERED_CURRENT_EXACT_PLAYER_GAME_PRIMITIVES.csv')]:
  for r in read(one(root,pat)):
   k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
   for z in REQP: putp(k,z,r[z])
 for r in read(one(a.pbp,'TREB_143_V2_PBP_EXACT_AUDIT.csv')):
  if r.get('status')=='PASS_EXACT' and str(r.get('validation_pass')).lower() in {'true','1'}:
   k=(gid(r['game_id']),tid(r['team_id']))
   for z in REQT: putt(k,z,r[z])
 for r in read(one(a.game,'RECOVERED_EXACT_TEAM_GAME_FACTS.csv')):
  k=(gid(r['game_id']),tid(r['team_id']))
  for z in REQT: putt(k,z,r[z])
 roster=collections.defaultdict(set); secs={}
 with gzip.open('team_trb_all_players/impact_database/roster_tenure_v3/player_game_roster_ledger.csv.gz','rt',encoding='utf-8',newline='') as f:
  for r in csv.DictReader(f):
   try:k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
   except:continue
   roster[k[:2]].add(k[2]); secs[k]=float(r.get('seconds_game') or 0)
 needp=set(); needt=set()
 for r in read(one(a.current,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv')):
  g=gid(r['game_id'])
  for x in str(r.get('player_targets') or '').split('|'):
   if x.strip():
    t,p=x.split(':',1); needp.add((g,tid(t),pid(p)))
  for t in str(r.get('team_ids') or '').split('|'):
   if t.strip(): needt.add((g,tid(t)))
 def fullp(k):
  if all(z in facts.get(k,{}) for z in REQP): return {z:facts[k][z] for z in REQP}
  if k in secs and abs(secs[k])<=1e-9:return {z:0.0 for z in REQP}
  return None
 rp={}; rt={}; rounds=0; changed=True
 while changed and rounds<12:
  changed=False; rounds+=1
  groups=sorted(set(k[:2] for k in needp)|needt)
  for gt in groups:
   ps=sorted(roster.get(gt,set()))
   if not ps: continue
   if gt in needt and not all(z in team.get(gt,{}) for z in REQT):
    vals=[]; ok=True
    for p in ps:
     v=fullp((gt[0],gt[1],p))
     if v is None:ok=False;break
     vals.append(v)
    if ok:
     nums=[sum(v[z] for v in vals) for z in REQP[1:]]
     if all(abs(x/5-round(x/5))<=1e-9 for x in nums):
      v=dict(zip(REQT,[float(round(x/5)) for x in nums]))
      for z in REQT: putt(gt,z,v[z])
      rt[gt]=v; changed=True
   if all(z in team.get(gt,{}) for z in REQT):
    for k in sorted(x for x in needp if x[:2]==gt and fullp(x) is None):
     others=[]; ok=True
     for p in ps:
      kk=(gt[0],gt[1],p)
      if kk==k:continue
      v=fullp(kk)
      if v is None:ok=False;break
      others.append(v)
     if not ok or k not in secs:continue
     v={'seconds_on':secs[k]}; valid=True
     for pz,tz in (('team_oreb_on','team_oreb'),('team_dreb_on','team_dreb'),('opponent_oreb_on','opponent_oreb'),('opponent_dreb_on','opponent_dreb')):
      x=5*team[gt][tz]-sum(o[pz] for o in others)
      if x < -1e-9 or abs(x-round(x))>1e-9:valid=False;break
      v[pz]=float(round(x))
     if valid:
      for z in REQP: putp(k,z,v[z])
      rp[k]=v; changed=True
 # exact conservation verification for all recovered players
 for k,v in rp.items():
  gt=k[:2]
  for pz,tz in (('team_oreb_on','team_oreb'),('team_dreb_on','team_dreb'),('opponent_oreb_on','opponent_oreb'),('opponent_dreb_on','opponent_dreb')):
   vals=[fullp((gt[0],gt[1],p)) for p in roster[gt]]
   assert all(x is not None for x in vals)
   assert abs(sum(x[pz] for x in vals)-5*team[gt][tz])<=1e-9,(k,pz)
 ppath=out/'COMPLEMENT_PLAYER.csv'; tpath=out/'COMPLEMENT_TEAM.csv'
 with open(ppath,'w',newline='') as f:
  cols=['game_id','team_id','player_id',*REQP,'provenance']; w=csv.DictWriter(f,fieldnames=cols); w.writeheader();
  for (g,t,p),v in sorted(rp.items()):w.writerow({'game_id':g,'team_id':t,'player_id':p,**v,'provenance':'exact five-player conservation complement'})
 with open(tpath,'w',newline='') as f:
  cols=['game_id','team_id',*REQT,'provenance']; w=csv.DictWriter(f,fieldnames=cols); w.writeheader();
  for (g,t),v in sorted(rt.items()):w.writerow({'game_id':g,'team_id':t,**v,'provenance':'exact five-player conservation from complete exact roster-player primitives'})
 qa={'status':'PASS','rounds':rounds,'target_player_gaps':len(needp),'target_team_gaps':len(needt),'recovered_exact_player_primitives':len(rp),'recovered_exact_team_facts':len(rt),'remaining_player_gaps':sum(fullp(k) is None for k in needp),'remaining_team_gaps':sum(not all(z in team.get(k,{}) for z in REQT) for k in needt),'integrity':{'five_player_conservation_only':True,'exact_roster_ledger_seconds':True,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
 (out/'COMPLEMENT_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n'); print(json.dumps(qa,indent=2,sort_keys=True))
if __name__=='__main__':main()
