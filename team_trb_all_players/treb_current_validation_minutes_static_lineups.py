#!/usr/bin/env python3
"""Exact minutes repair for current BLOCKED_VALIDATION TREB rows.

Uses only the already-proven tenure game sets plus pinned historical NBA Stats and
NBA Stats V3 play-by-play chronology. Reconstructs complete lineups with the
validated production lineup engine; rebound joining/classification is not used.
A target passes only when every proven tenure game reconstructs, both teams pass
5-player-seconds invariants, and aggregate seconds are within the unchanged
60-second structural gate against the canonical target.
"""
from __future__ import annotations
import argparse,csv,gzip,json,math,pathlib,tempfile
from collections import defaultdict
import pandas as pd
import local_treb_rebuild as core
import production_treb_engine_v3 as lineup
import run_local_treb_production as io
import treb_static_period_unique_recovery as static

GATE=60.0

def sid(v):
 s=str(v).strip(); return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s
def tid(v): return str(int(float(v)))
def gid(v): return int(float(str(v).strip()))
def finite(v):
 try:
  x=float(v); return x if math.isfinite(x) else None
 except:return None

def duration_seconds(game):
 p=int(pd.to_numeric(game['PERIOD'],errors='coerce').max()); return 2880+max(0,p-4)*300

def target_seconds(repo_root,wanted):
 p=repo_root/'team_trb_all_players/impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz'
 out={}
 with gzip.open(p,'rt',encoding='utf-8') as f:
  for line in f:
   if not line.strip(): continue
   r=json.loads(line); k=(str(r.get('season')),tid(r.get('team_id')),sid(r.get('player_id')))
   if k not in wanted: continue
   sec=finite(r.get('seconds_on'))
   if sec is None:
    m=finite(r.get('minutes_on')); sec=None if m is None else m*60
   out[k]=sec
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',default='.'); ap.add_argument('--proof',required=True); ap.add_argument('--season',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
 root=pathlib.Path(a.repo_root); out=pathlib.Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 proof=list(csv.DictReader(open(a.proof,newline='',encoding='utf-8')))
 proof=[r for r in proof if r.get('prior_status')=='BLOCKED_VALIDATION' and r.get('status','').startswith('EXACT_') and r.get('season')==a.season]
 if not proof:
  (out/'QA.json').write_text(json.dumps({'status':'NO_TARGETS','season':a.season,'targets':0},indent=2)+'\n'); return 0
 wanted={(r['season'],tid(r['team_id']),sid(r['player_id'])) for r in proof}
 ts=target_seconds(root,wanted)
 if set(ts)!=wanted: raise RuntimeError(f'target seconds drift wanted={len(wanted)} found={len(ts)}')
 tasks={}
 all_games=set()
 for r in proof:
  k=(r['season'],tid(r['team_id']),sid(r['player_id']))
  games=[gid(x) for x in str(r.get('game_ids','')).split('|') if x]
  tasks[k]={'games':games,'target_seconds':ts[k]}; all_games.update(games)
 qa={'status':'PASS','season':a.season,'targets':len(tasks),'games_requested':len(all_games),'games_reconstructed':0,'game_failures':[],'targets_with_complete_exact_seconds':0,'targets_within_60s_gate':0,'integrity':{'proven_tenure_games_only':True,'validated_production_lineup_engine':True,'complete_lineup_reconstruction_required':True,'team_player_seconds_invariant_required':True,'rebound_join_used':False,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'missing_game_zero_filled':False,'promotion_performed':False}}
 per_game=[]; recon={}
 with tempfile.TemporaryDirectory(prefix='treb_min_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_min_arc_') as td:
  try:
   repo=static.prep(pathlib.Path(gd)); tmp=pathlib.Path(td); year=a.season[:4]
   nr,_=static.archive_df(repo,tmp,'nbastats',year,all_games); vr,_=static.archive_df(repo,tmp,'nbastatsv3',year,all_games)
   nba=io.normalize_nba(nr); v3=lineup.normalize_v3(vr)
  except Exception as e:
   qa['status']='SOURCE_FAILURE'; qa['game_failures'].append({'scope':'source','error':f'{type(e).__name__}: {e}'}); (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa,indent=2)); return 0
 ng={gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}
 for g in sorted(all_games):
  if g not in ng or g not in vg:
   qa['game_failures'].append({'game_id':g,'status':'SOURCE_SET_GAP','nba':g in ng,'v3':g in vg}); continue
  try:
   lg=lineup.reconstruct_game_lineups(ng[g],vg[g]); pt=core._player_team(ng[g]); teams=sorted(set(int(x) for x in pt.values())); dur=duration_seconds(ng[g])
   if len(teams)!=2: raise ValueError(f'team identity count={len(teams)}')
   sec={int(p):int(round(float(s))) for p,s in lg.seconds.items() if float(s)>0}
   for t in teams:
    observed=sum(s for p,s in sec.items() if pt.get(p)==t); expected=dur*5
    if observed!=expected: raise ValueError(f'team player-seconds mismatch team={t} observed={observed} expected={expected}')
   recon[g]=(sec,pt,dur); qa['games_reconstructed']+=1
  except Exception as e:
   qa['game_failures'].append({'game_id':g,'status':'RECONSTRUCTION_FAIL','error':f'{type(e).__name__}: {e}'})
 results=[]
 for k,t in sorted(tasks.items()):
  missing=[g for g in t['games'] if g not in recon]; vals=[]; team_mismatch=[]
  if not missing:
   pid=int(k[2]); team=int(k[1])
   for g in t['games']:
    sec,pt,dur=recon[g]; s=int(sec.get(pid,0))
    if pid in sec and int(pt.get(pid,-1))!=team: team_mismatch.append(g)
    vals.append((g,s)); per_game.append({'season':a.season,'team_id':team,'player_id':pid,'game_id':g,'seconds_on':s,'duration_seconds':dur})
  complete=not missing and not team_mismatch
  total=sum(v for _,v in vals) if complete else None; target=t['target_seconds']; delta=None if total is None or target is None else total-target
  within=bool(complete and delta is not None and abs(delta)<=GATE+1e-9)
  if complete: qa['targets_with_complete_exact_seconds']+=1
  if within: qa['targets_within_60s_gate']+=1
  results.append({'season':k[0],'team_id':k[1],'player_id':k[2],'proven_games':len(t['games']),'games_reconstructed':len(vals),'complete_exact':complete,'aggregate_seconds':total,'target_seconds':target,'delta_seconds':delta,'within_60s_gate':within,'missing_games':'|'.join(map(str,missing)),'team_identity_mismatch_games':'|'.join(map(str,team_mismatch))})
 pd.DataFrame(results).to_csv(out/'TARGET_RESULTS.csv',index=False)
 if per_game: pd.DataFrame(per_game).to_csv(out/'PLAYER_GAME_EXACT_SECONDS.csv.gz',index=False,compression='gzip')
 if qa['games_reconstructed']<len(all_games): qa['status']='PARTIAL'
 (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa,indent=2),flush=True); return 0

if __name__=='__main__': raise SystemExit(main())
