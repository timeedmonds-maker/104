import argparse,csv,gzip,json,pathlib,collections,shutil,glob
REQP=('seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on')
REQT=('team_oreb','team_dreb','opponent_oreb','opponent_dreb')
def pid(x): return str(x).strip().removesuffix('.0')
def tid(x): return str(int(float(x)))
def gid(x):
 s=str(x).strip().removesuffix('.0')
 try:return str(int(float(s))).zfill(10)
 except:return s.zfill(10)
def key(r): return (str(r['season']),tid(r['team_id']),pid(r['player_id']))
def one(root,pat):
 m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]
def rows(path):
 p=pathlib.Path(path)
 if p.suffix=='.gz':
  with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
 with open(p,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def write(path,rs,fields=None,gz=False):
 path=pathlib.Path(path)
 if fields is None: fields=sorted({k for r in rs for k in r}) if rs else []
 op=(lambda: gzip.open(path,'wt',encoding='utf-8',newline='')) if gz else (lambda: open(path,'w',newline=''))
 with op() as f:
  if fields:
   w=csv.DictWriter(f,fieldnames=list(fields)); w.writeheader(); w.writerows(rs)
def main():
 ap=argparse.ArgumentParser()
 for x in ['current','consensus','pbp','old','mid','newer','game','currentcons','out']: ap.add_argument('--'+x,required=True)
 ap.add_argument('--candidate-player-dir'); ap.add_argument('--candidate-team-dir')
 a=ap.parse_args(); OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)
 current=rows(one(a.current,'AUTONOMOUS_BLOCKER_MANIFEST.csv')); prior=rows(one(a.current,'TREB_CUMULATIVE_EXACT_PROMOTED.csv')); mat=rows(one(a.current,'TREB_MATERIALITY_ACCEPTED.csv')); diag0=rows(one(a.current,'TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv'))
 assert len(current)==563 and len(prior)==683 and len(mat)==4,(len(current),len(prior),len(mat))
 cur={key(r):r for r in current}; noid={key(r) for r in diag0 if r.get('status')=='NO_EXACT_TENURE_IDENTITY'}
 facts=collections.defaultdict(dict); fsrc=collections.defaultdict(dict); team=collections.defaultdict(dict); tsrc=collections.defaultdict(dict); stale=[]
 def putp(k,z,v,src,auth=False):
  v=float(v); old=facts[k].get(z); os=fsrc[k].get(z)
  if old is None: facts[k][z]=v; fsrc[k][z]=src; return
  if abs(old-v)<=1e-9:return
  if os=='current_shared': stale.append(('player',k,z,old,v,src)); return
  if auth: stale.append(('player',k,z,v,old,os)); facts[k][z]=v; fsrc[k][z]=src; return
  raise SystemExit(f'PLAYER_CONFLICT {k} {z} {old} {os} {v} {src}')
 def putt(k,z,v,src,auth=False):
  v=float(v); old=team[k].get(z); os=tsrc[k].get(z)
  if old is None: team[k][z]=v; tsrc[k][z]=src; return
  if abs(old-v)<=1e-9:return
  if os=='current_shared': stale.append(('team',k,z,old,v,src)); return
  if auth: stale.append(('team',k,z,v,old,os)); team[k][z]=v; tsrc[k][z]=src; return
  raise SystemExit(f'TEAM_CONFLICT {k} {z} {old} {os} {v} {src}')
 # controlling shared checkpoint first
 for r in rows(one(a.current,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz')):
  k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
  for z in REQP: putp(k,z,r[z],'current_shared',True)
 for r in rows(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')):
  k=(gid(r['game_id']),tid(r['team_id']))
  for z in REQT: putt(k,z,r[z],'current_shared',True)
 # older exact layers only gap-fill
 for r in rows(one(a.consensus,'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv')):
  k=(gid(r['game_id']),tid(r['team_id']),pid(r.get('player_id','')))
  if r['field'] in REQP: putp(k,r['field'],r['value'],'consensus')
  elif not k[2] and r['field'] in REQT: putt((k[0],k[1]),r['field'],r['value'],'consensus')
 for root,pat,src in [(a.old,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','old'),(a.mid,'RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','mid'),(a.newer,'RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','newer')]:
  for r in rows(one(root,pat)):
   k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
   for z in REQP: putp(k,z,r[z],src)
 for root,pat,src in [(a.game,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv','game'),(a.currentcons,'RECOVERED_CURRENT_EXACT_PLAYER_GAME_PRIMITIVES.csv','currentcons')]:
  for r in rows(one(root,pat)):
   k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
   for z in REQP: putp(k,z,r[z],src)
 for r in rows(one(a.pbp,'TREB_143_V2_PBP_EXACT_AUDIT.csv')):
  if r.get('status')=='PASS_EXACT' and str(r.get('validation_pass')).lower() in {'true','1'}:
   k=(gid(r['game_id']),tid(r['team_id']))
   for z in REQT: putt(k,z,r[z],'pbp_exact')
 for r in rows(one(a.game,'RECOVERED_EXACT_TEAM_GAME_FACTS.csv')):
  k=(gid(r['game_id']),tid(r['team_id']))
  for z in REQT: putt(k,z,r[z],'game_exact')
 # candidates: only admit files whose corresponding recovery QA passed
 cand_p=[]; cand_t=[]
 if a.candidate_player_dir and pathlib.Path(a.candidate_player_dir).exists():
  q=list(pathlib.Path(a.candidate_player_dir).rglob('PLAYER_GAME_PBPSTATS_RECOVERY_QA.json'))
  if q:
   qq=json.loads(q[0].read_text())
   if qq.get('gate_pass') is True and int(qq.get('control_mismatches',-1))==0:
    for p in pathlib.Path(a.candidate_player_dir).rglob('RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'): cand_p+=rows(p)
 if a.candidate_team_dir and pathlib.Path(a.candidate_team_dir).exists():
  q=list(pathlib.Path(a.candidate_team_dir).rglob('TEAM_FACT_SOURCE_RACE_QA.json'))
  if q:
   qq=json.loads(q[0].read_text())
   if int(qq.get('promoted_target_team_facts',qq.get('targets_promoted',0)) or 0)>0:
    for p in pathlib.Path(a.candidate_team_dir).rglob('*.csv*'):
     try:
      rr=rows(p)
      if rr and {'game_id','team_id',*REQT}.issubset(rr[0]): cand_t+=rr
     except: pass
 for r in cand_p:
  k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
  for z in REQP:
   old=facts[k].get(z); v=float(r[z])
   if old is not None and abs(old-v)>1e-9: raise SystemExit(f'CANDIDATE_PLAYER_CONFLICT {k} {z}')
   if old is None: facts[k][z]=v; fsrc[k][z]='direct_candidate'
 for r in cand_t:
  k=(gid(r['game_id']),tid(r['team_id']))
  for z in REQT:
   old=team[k].get(z); v=float(r[z])
   if old is not None and abs(old-v)>1e-9: raise SystemExit(f'CANDIDATE_TEAM_CONFLICT {k} {z}')
   if old is None: team[k][z]=v; tsrc[k][z]='direct_candidate'
 # targets and exact ledger game membership. Fail closed unless roster ledger count exactly equals target tenure-game count.
 targets={}
 with gzip.open('team_trb_all_players/impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz','rt',encoding='utf-8') as f:
  for line in f:
   if line.strip():
    r=json.loads(line); k=key(r)
    if k in cur: targets[k]=r
 assert len(targets)==563,len(targets)
 games=collections.defaultdict(set); ledger_seconds={}
 with gzip.open('team_trb_all_players/impact_database/roster_tenure_v3/player_game_roster_ledger.csv.gz','rt',encoding='utf-8',newline='') as f:
  for r in csv.DictReader(f):
   try:k=key(r)
   except:continue
   if k in cur:
    g=gid(r['game_id']); games[k].add(g); ledger_seconds[(k,g)]=float(r.get('seconds_game') or 0)
 new=[]; diag=[]; reasons=collections.Counter()
 for k in sorted(cur):
  t=targets[k]; expected=int(float(t.get('team_games_in_tenure') or 0)); gs=games.get(k,set())
  if k in noid or not gs or len(gs)!=expected:
   reasons['NO_EXACT_TENURE_IDENTITY']+=1; diag.append({'season':k[0],'team_id':k[1],'player_id':k[2],'status':'NO_EXACT_TENURE_IDENTITY','missing_team_count':0,'missing_player_count':0,'bad_count':0,'minutes_delta_seconds':'','detail':''}); continue
  agg=collections.Counter(); mt=[]; mp=[]; bad=[]
  for g in sorted(gs):
   tv=team.get((g,k[1]),{})
   if not all(z in tv for z in REQT): mt.append(g); continue
   pv=facts.get((g,k[1],k[2]),{})
   if not all(z in pv for z in REQP):
    sec=ledger_seconds.get((k,g))
    if sec is not None and abs(sec)<=1e-9: pv={z:0.0 for z in REQP}
    else: mp.append(g); continue
   if min(tv['team_oreb']-pv['team_oreb_on'],tv['team_dreb']-pv['team_dreb_on'],tv['opponent_oreb']-pv['opponent_oreb_on'],tv['opponent_dreb']-pv['opponent_dreb_on']) < -1e-9: bad.append(g); continue
   for z in REQP: agg[z]+=pv[z]
   for z in REQT: agg[z]+=tv[z]
  delta=abs(agg['seconds_on']-float(t.get('seconds_on') or 0))
  if mt or mp or bad or delta>60:
   st='BLOCKED_MISSING_PRIMITIVES' if (mt or mp) else 'BLOCKED_VALIDATION'; reasons[st]+=1; diag.append({'season':k[0],'team_id':k[1],'player_id':k[2],'status':st,'missing_team_count':len(mt),'missing_player_count':len(mp),'bad_count':len(bad),'minutes_delta_seconds':delta,'detail':json.dumps({'missing_team':mt,'missing_player':mp,'bad':bad},separators=(',',':'))}); continue
  tr_on=agg['team_oreb_on']+agg['team_dreb_on']; op_on=agg['opponent_oreb_on']+agg['opponent_dreb_on']; tr=agg['team_oreb']+agg['team_dreb']; op=agg['opponent_oreb']+agg['opponent_dreb']; tr_off=tr-tr_on; op_off=op-op_on
  if min(tr_on,op_on,tr_off,op_off)<-1e-9 or tr_on+op_on<=0 or tr_off+op_off<=0:
   reasons['BLOCKED_DENOMINATOR_OR_NEGATIVE']+=1; continue
  on=100*tr_on/(tr_on+op_on); off=100*tr_off/(tr_off+op_off)
  new.append({'metric':'TotalReboundPct','off_corrected':off,'on':on,'on_minus_off_corrected':on-off,'player_id':k[2],'provenance':'direct 563 exact reclosure: controlling shared checkpoint + zero-conflict direct exact candidate gap fills + older exact layers','season':k[0],'seconds_on':agg['seconds_on'],'team_games_in_tenure':len(gs),'team_id':k[1]}); reasons['PROMOTED_EXACT']+=1
 newkeys={key(r) for r in new}; cumulative=prior+new; remain=[r for r in current if key(r) not in newkeys]
 assert len(cumulative)==683+len(new) and len(remain)==563-len(new) and len(cumulative)+len(remain)+len(mat)==1250
 # persist enlarged controlling shared ledgers for next cycle
 sharedp=rows(one(a.current,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz')); seen={(gid(r['game_id']),tid(r['team_id']),pid(r['player_id'])) for r in sharedp}
 for r in cand_p:
  k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
  if k not in seen: sharedp.append(r); seen.add(k)
 sharedt=rows(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')); seent={(gid(r['game_id']),tid(r['team_id'])) for r in sharedt}
 for r in cand_t:
  k=(gid(r['game_id']),tid(r['team_id']))
  if k not in seent: sharedt.append(r); seent.add(k)
 write(OUT/'TREB_NEW_EXACT_PROMOTED.csv',new); write(OUT/'TREB_CUMULATIVE_EXACT_PROMOTED.csv',cumulative); write(OUT/'AUTONOMOUS_BLOCKER_MANIFEST.csv',remain,current[0].keys()); write(OUT/'TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv',diag); write(OUT/'TREB_MATERIALITY_ACCEPTED.csv',mat)
 write(OUT/'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz',sharedp,sharedp[0].keys(),True); write(OUT/'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz',sharedt,sharedt[0].keys(),True)
 qa={'status':'PASS','starting_exact_full_core':9080,'starting_production_resolved_full_core':9084,'starting_residual':563,'direct_candidate_player_rows':len(cand_p),'direct_candidate_team_rows':len(cand_t),'new_exact_promotions':len(new),'ending_exact_full_core':9080+len(new),'ending_production_resolved_full_core':9084+len(new),'ending_residual':563-len(new),'reason_counts':dict(sorted(reasons.items())),'integrity':{'raw_exact_counts_only':True,'minutes_gate_seconds':60.0,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False,'partial_tenure_subtraction_used':False}}
 (OUT/'TREB_SHARED_GAME_RECLOSURE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n'); print(json.dumps(qa,indent=2,sort_keys=True))
if __name__=='__main__': main()
