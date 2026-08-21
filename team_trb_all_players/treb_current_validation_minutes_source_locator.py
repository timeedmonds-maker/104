#!/usr/bin/env python3
"""Locate independent retained exact per-game minutes sources for the current 19 TREB validation rows.

Diagnostic-only. Uses the already-proven schedule-audited tenure game sets and canonical target
seconds, then scans retained impact_database tabular sources. A source is reported as MATCH only
when it has exactly one finite player-game value for every proven tenure game and the aggregate is
within the unchanged 60-second structural gate. No missing game is treated as zero.
"""
from __future__ import annotations
import csv,gzip,json,math,pathlib,argparse
from collections import defaultdict

GATE=60.0

def sid(v):
 s=str(v).strip(); return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s
def gid(v):
 s=sid(v)
 try:return str(int(float(s))).zfill(10)
 except:return s.zfill(10)
def tid(v):
 try:return str(int(float(v)))
 except:return str(v).strip()
def fin(v):
 try:
  x=float(v); return x if math.isfinite(x) else None
 except:return None

def norm_fields(fs): return {str(x).strip().lower():x for x in (fs or [])}
def pick(m,names):
 for n in names:
  if n in m:return m[n]
 return None

def iter_rows(p):
 name=p.name.lower()
 if name.endswith('.csv.gz'):
  with gzip.open(p,'rt',encoding='utf-8',errors='replace',newline='') as f:
   rd=csv.DictReader(f); yield ('schema',rd.fieldnames); yield from rd
 elif name.endswith('.csv'):
  with p.open('r',encoding='utf-8',errors='replace',newline='') as f:
   rd=csv.DictReader(f); yield ('schema',rd.fieldnames); yield from rd
 elif name.endswith('.jsonl.gz'):
  with gzip.open(p,'rt',encoding='utf-8',errors='replace') as f:
   first=True
   for line in f:
    if not line.strip():continue
    try:r=json.loads(line)
    except:continue
    if not isinstance(r,dict):continue
    if first:yield ('schema',list(r));first=False
    yield r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--repo-root',default='.');ap.add_argument('--proof',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
 root=pathlib.Path(a.repo_root);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
 proof=list(csv.DictReader(open(a.proof,newline='',encoding='utf-8')))
 proof=[r for r in proof if r.get('prior_status')=='BLOCKED_VALIDATION' and r.get('status','').startswith('EXACT_')]
 if len(proof)!=19:raise RuntimeError(f'PROOF_DRIFT {len(proof)}')
 targets={}
 tp=root/'team_trb_all_players/impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz'
 wanted={(r['season'],tid(r['team_id']),sid(r['player_id'])) for r in proof}
 with gzip.open(tp,'rt',encoding='utf-8') as f:
  for line in f:
   if not line.strip():continue
   r=json.loads(line);k=(str(r.get('season')),tid(r.get('team_id')),sid(r.get('player_id')))
   if k in wanted:targets[k]=r
 tasks={}
 for r in proof:
  k=(r['season'],tid(r['team_id']),sid(r['player_id']));t=targets[k]
  sec=fin(t.get('seconds_on'))
  if sec is None:
   m=fin(t.get('minutes_on'));sec=None if m is None else m*60
  games=[gid(x) for x in str(r.get('game_ids','')).split('|') if x]
  tasks[k]={'games':games,'target_seconds':sec}
 base=root/'team_trb_all_players/impact_database'
 files=[]
 for p in base.rglob('*'):
  q=p.name.lower()
  if p.is_file() and (q.endswith('.csv') or q.endswith('.csv.gz') or q.endswith('.jsonl.gz')) and any(z in str(p).lower() for z in ('minute','game','roster','box','player','fact','ledger')):
   if p==tp:continue
   files.append(p)
 results=[];schemas=[]
 for p in sorted(files):
  try:
   it=iter_rows(p);first=next(it,None)
   if not first or first[0]!='schema':continue
   fs=first[1] or [];m=norm_fields(fs)
   pf=pick(m,['player_id','personid','person_id','playerid']);gf=pick(m,['game_id','gameid']);vf=pick(m,['seconds_game','seconds','player_seconds','seconds_on','minutes','min','player_minutes','minutes_on'])
   if not (pf and gf and vf):continue
   sf=pick(m,['season','season_id']);tf=pick(m,['team_id','teamid','team_id_official'])
   schemas.append({'path':str(p.relative_to(root)),'player_field':pf,'game_field':gf,'value_field':vf,'season_field':sf or '','team_field':tf or ''})
   vals=defaultdict(lambda:defaultdict(list))
   for r in it:
    if not isinstance(r,dict):continue
    pp=sid(r.get(pf));gg=gid(r.get(gf));vv=fin(r.get(vf))
    if vv is None:continue
    for k,t in tasks.items():
     if pp!=k[2] or gg not in t['games']:continue
     if sf and str(r.get(sf)) and str(r.get(sf))!=k[0]:continue
     if tf and str(r.get(tf)).strip() and tid(r.get(tf))!=k[1]:continue
     vals[k][gg].append(vv)
   for k,t in tasks.items():
    one={g:vs[0] for g,vs in vals[k].items() if len(vs)==1}
    complete=len(one)==len(t['games']) and set(one)==set(t['games'])
    total=sum(one.values()) if complete else None;delta=None if total is None or t['target_seconds'] is None else total-t['target_seconds']
    if vals[k]:results.append({'season':k[0],'team_id':k[1],'player_id':k[2],'path':str(p.relative_to(root)),'value_field':vf,'proven_games':len(t['games']),'games_with_rows':len(vals[k]),'games_unique':len(one),'complete_unique':complete,'aggregate_seconds':total,'target_seconds':t['target_seconds'],'delta_seconds':delta,'within_60s_gate':bool(complete and delta is not None and abs(delta)<=GATE+1e-9)})
  except Exception as exc:
   schemas.append({'path':str(p.relative_to(root)),'error':repr(exc)})
 fields=sorted({x for r in results for x in r}) if results else ['season','team_id','player_id','path']
 with (out/'TREB_CURRENT_19_MINUTES_SOURCE_CANDIDATES.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(results)
 matches=[r for r in results if r.get('within_60s_gate')]
 with (out/'TREB_CURRENT_19_MINUTES_EXACT_MATCHES.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(matches)
 with (out/'TREB_CURRENT_19_MINUTES_SCHEMAS.csv').open('w',newline='',encoding='utf-8') as f:
  fs=sorted({x for r in schemas for x in r}) if schemas else ['path'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(schemas)
 by=defaultdict(int)
 for r in matches:by[(r['season'],r['team_id'],r['player_id'])]+=1
 qa={'status':'PASS_DIAGNOSTIC','validation_rows':19,'tabular_sources_with_usable_schema':len([r for r in schemas if 'error' not in r]),'source_read_errors':len([r for r in schemas if 'error' in r]),'candidate_aggregates':len(results),'exact_source_matches':len(matches),'validation_rows_with_at_least_one_exact_source':len(by),'minutes_gate_seconds':GATE,'promotion_performed':False,'integrity':{'proven_schedule_tenure_games_only':True,'complete_game_coverage_required':True,'one_row_per_game_required':True,'missing_games_zero_filled':False,'canonical_target_seconds_required':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False}}
 (out/'TREB_CURRENT_19_MINUTES_SOURCE_LOCATOR_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(qa,indent=2,sort_keys=True))
if __name__=='__main__':main()
