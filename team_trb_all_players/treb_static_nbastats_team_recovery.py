import argparse,csv,gzip,json,pathlib,tarfile,re,collections

def gid(x):
 s=str(x).strip().removesuffix('.0')
 try:return str(int(float(s))).zfill(10)
 except:return s.zfill(10)
def tid(x): return str(int(float(x)))
def season_start(s): return int(str(s).split('-')[0])
def one(root,pat):
 m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]
def read(p):
 p=pathlib.Path(p)
 if p.suffix=='.gz':
  with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
 with open(p,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def norm(s):return re.sub(r'[^a-z0-9]','',str(s).lower())
GKEY={'gameid','game_id','gameId'}; TKEY={'teamid','team_id','teamId'}
OREB_KEYS={'oreb','offensiverebounds','reboundsoffensive','offensiveRebounds'}
DREB_KEYS={'dreb','defensiverebounds','reboundsdefensive','defensiveRebounds'}
def pick(d,keys):
 nd={norm(k):v for k,v in d.items()}
 for k in keys:
  if norm(k) in nd:return nd[norm(k)]
 return None
def rows_from_obj(obj, inherited_game=None):
 out=[]
 def walk(x, game=None):
  if isinstance(x,dict):
   g=pick(x,GKEY) or game
   # NBA stats resultSet/resultSets tables
   h=x.get('headers'); rs=x.get('rowSet') or x.get('row_set')
   if isinstance(h,list) and isinstance(rs,list):
    nh=[norm(z) for z in h]
    def ix(opts):
     for o in opts:
      no=norm(o)
      if no in nh:return nh.index(no)
     return None
    gi,ti,oi,di=ix(GKEY),ix(TKEY),ix(OREB_KEYS),ix(DREB_KEYS)
    if ti is not None and oi is not None and di is not None:
     for r in rs:
      if isinstance(r,(list,tuple)) and len(r)>max(ti,oi,di,gi or 0):
       gg=r[gi] if gi is not None else g
       if gg is not None:out.append((gid(gg),tid(r[ti]),float(r[oi]),float(r[di]),'resultSet'))
   # direct team-stat dictionaries / V3 shapes
   tv=pick(x,TKEY); ov=pick(x,OREB_KEYS); dv=pick(x,DREB_KEYS)
   if tv is not None and ov is not None and dv is not None and g is not None:
    try:out.append((gid(g),tid(tv),float(ov),float(dv),'dict'))
    except:pass
   for v in x.values():walk(v,g)
  elif isinstance(x,list):
   for v in x:walk(v,game)
 walk(obj,inherited_game)
 return out
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--current',required=True); ap.add_argument('--archives',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
 OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)
 reg=read(one(a.current,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv')); shared=read(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
 game_season={gid(r['game_id']):r['season'] for r in reg}; targets=set()
 for r in reg:
  g=gid(r['game_id'])
  for t in str(r.get('team_ids') or '').split('|'):
   if t.strip():targets.add((g,tid(t)))
 exact={(gid(r['game_id']),tid(r['team_id'])):(float(r['team_oreb']),float(r['team_dreb'])) for r in shared}
 desired_games=set(game_season)|{g for g,t in exact}
 extracted=collections.defaultdict(set); evidence=[]; archive_stats=[]
 for arc in sorted(pathlib.Path(a.archives).glob('nbastats_*.tar.xz')):
  yr=int(re.search(r'nbastats_(\d{4})',arc.name).group(1)); members=parsed=matched=0
  with tarfile.open(arc,'r:xz') as tf:
   for m in tf:
    if not m.isfile():continue
    members+=1
    name=m.name
    name_gid=next((g for g in desired_games if g in name or g.lstrip('0') in name),None)
    # JSON-like payloads only; if filename doesn't identify desired game, cheaply scan bytes for desired IDs.
    if not any(name.lower().endswith(ext) for ext in ('.json','.txt','.jsonl','')):continue
    try:
     b=tf.extractfile(m).read()
    except:continue
    if not name_gid:
     hits=[g for g in desired_games if g.encode() in b or g.lstrip('0').encode() in b]
     if not hits:continue
     name_gid=hits[0]
    try:
     text=b.decode('utf-8-sig','ignore').strip(); objs=[]
     try:objs=[json.loads(text)]
     except:
      objs=[]
      for line in text.splitlines():
       try:objs.append(json.loads(line))
       except:pass
     if not objs:continue
     parsed+=1
     for obj in objs:
      for g,t,o,d,shape in rows_from_obj(obj,name_gid):
       if g not in desired_games:continue
       extracted[(g,t)].add((o,d)); evidence.append({'season':game_season.get(g,''),'game_id':g,'team_id':t,'team_oreb':o,'team_dreb':d,'archive':arc.name,'member':name,'shape':shape})
       matched+=1
    except:continue
  archive_stats.append({'archive':arc.name,'members':members,'parsed_desired_members':parsed,'team_rows_seen':matched})
 # require unique static value per team-game
 static={k:next(iter(v)) for k,v in extracted.items() if len(v)==1}; ambiguous={str(k):sorted(v) for k,v in extracted.items() if len(v)>1}
 controls=[]; mismatch=[]
 for k,v in sorted(exact.items()):
  if k in static:
   controls.append(k)
   if static[k] != v:mismatch.append({'game_id':k[0],'team_id':k[1],'expected_oreb':v[0],'expected_dreb':v[1],'static_oreb':static[k][0],'static_dreb':static[k][1]})
 seasons_with_controls={game_season.get(g,'') for g,t in controls if game_season.get(g,'')}
 # Gate: no mismatch, meaningful broad controls. If exact shared controls don't cover >=8 seasons, require >=30 comparisons across >=5 seasons.
 gate=(len(mismatch)==0 and len(controls)>=30 and len(seasons_with_controls)>=5)
 # Only target facts that were actually missing from controlling shared layer.
 recovered=[]
 if gate:
  for g,t in sorted(targets):
   if (g,t) in exact or (g,t) not in static:continue
   opps=sorted([x for x in targets if x[0]==g and x[1]!=t])
   if len(opps)!=1 or opps[0] not in static:continue
   o,d=static[(g,t)]; oo,od=static[opps[0]]
   recovered.append({'season':game_season[g],'game_id':g,'team_id':t,'team_oreb':o,'team_dreb':d,'opponent_oreb':oo,'opponent_dreb':od,'provenance':'pinned shufinskiy nba_data static NBA Stats team boxscore; zero-mismatch exact controls'})
 def write(p,rs,fields=None):
  if fields is None:fields=sorted({k for r in rs for k in r}) if rs else []
  with open(p,'w',newline='') as f:
   if fields:
    w=csv.DictWriter(f,fieldnames=list(fields));w.writeheader();w.writerows(rs)
 write(OUT/'STATIC_NBASTATS_TEAM_RECOVERED.csv',recovered,['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance'])
 write(OUT/'STATIC_NBASTATS_CONTROL_MISMATCHES.csv',mismatch,['game_id','team_id','expected_oreb','expected_dreb','static_oreb','static_dreb'])
 write(OUT/'STATIC_NBASTATS_EVIDENCE.csv',evidence)
 write(OUT/'STATIC_NBASTATS_ARCHIVE_STATS.csv',archive_stats)
 qa={'status':'PASS' if gate else 'FAIL_CLOSED','target_team_games':len(targets),'static_unique_team_games':len(static),'ambiguous_static_team_games':len(ambiguous),'exact_control_comparisons':len(controls),'control_seasons':len(seasons_with_controls),'control_mismatches':len(mismatch),'recovered_target_team_facts':len(recovered),'missing_target_team_facts_after':sum(1 for k in targets if k not in exact and k not in {(gid(r['game_id']),tid(r['team_id'])) for r in recovered}),'archives':archive_stats,'integrity':{'pinned_static_source_only':True,'zero_mismatch_required':True,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
 (OUT/'STATIC_NBASTATS_TEAM_RECOVERY_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n');print(json.dumps(qa,indent=2,sort_keys=True))
if __name__=='__main__':main()
