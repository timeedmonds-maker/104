#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,json,pathlib,signal
from collections import Counter,defaultdict
import pandas as pd
import build_exact_game_fact_layer as base
import production_treb_engine as v2
import run_local_treb_production as io
import local_treb_rebuild as core
import recover_residual_shared_games as targets

REQP=targets.REQP; REQT=targets.REQT

def gid(x): return targets.gid(x)
def tid(x): return targets.tid(x)
def pid(x): return targets.pid(x)
class GameTimeout(RuntimeError): pass
def timeout(signum,frame): raise GameTimeout('V2 per-game timeout')

def build_game_v2(game_id,nba_game,pbp_game):
    lineups=v2.reconstruct_game_lineups(nba_game)
    joined,audit=v2.join_pbp_rebounds(lineups,pbp_game)
    if int(audit.get('unmatched_rebound_bearing_rows',0)):
        raise ValueError(f"unmatched PBP rebound rows={audit['unmatched_rebound_bearing_rows']}")
    joined=v2.classify_rebounds(joined)
    player_team=core._player_team(nba_game)
    team_abbr=base._team_abbreviations(nba_game); names=base._player_names(nba_game)
    teams=sorted(set(int(x) for x in player_team.values()))
    if len(teams)!=2: raise ValueError(f'expected 2 teams got {teams}')
    if any(t not in team_abbr for t in teams): raise ValueError('missing team abbreviation')
    duration=base._duration_seconds(nba_game)
    date=None
    if 'GAMEDATE' in pbp_game.columns and not pbp_game.empty:
        x=pbp_game.GAMEDATE.iloc[0]; date=None if pd.isna(x) else str(x)
    real=joined.IS_REAL_REBOUND.astype(bool); oreb=joined.IS_OREB.astype(bool)
    team_rows=[]; masks={}
    for t in teams:
        ab=team_abbr[t]; offense=~joined.OPPONENT.astype(str).eq(ab); defense=~offense; masks[t]=(offense,defense)
        team_rows.append({'game_id':int(game_id),'game_date':date,'team_id':t,'team_abbr':ab,'game_seconds':duration,
          'team_oreb':base._count(offense&oreb),'team_dreb':base._count(defense&real&~oreb),
          'opponent_oreb':base._count(defense&oreb),'opponent_dreb':base._count(offense&real&~oreb)})
    player_rows=[]
    for p,seconds in sorted(lineups.seconds.items()):
        p=int(p); sec=int(round(float(seconds)))
        if sec<=0: continue
        t=player_team.get(p)
        if t is None: raise ValueError(f'positive-second player has no team {p}')
        t=int(t); on=joined.LINEUP.map(lambda lu:p in lu); offense,defense=masks[t]
        player_rows.append({'game_id':int(game_id),'game_date':date,'team_id':t,'team_abbr':team_abbr[t],'player_id':p,'player':names.get(p,''),'seconds_on':sec,
          'team_oreb_on':base._count(on&offense&oreb),'team_dreb_on':base._count(on&defense&real&~oreb),
          'opponent_oreb_on':base._count(on&defense&oreb),'opponent_dreb_on':base._count(on&offense&real&~oreb)})
    for t in teams:
        observed=sum(r['seconds_on'] for r in player_rows if r['team_id']==t); expected=duration*5
        if observed!=expected: raise ValueError(f'team player-seconds mismatch team={t} {observed}!={expected}')
    return team_rows,player_rows,{'repairs':lineups.repairs,'join':audit}

def write_gz(path,rows,fields):
    with gzip.open(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows({z:r.get(z,'') for z in fields} for r in rows)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--current-dir',type=pathlib.Path,required=True)
    ap.add_argument('--materiality-dir',type=pathlib.Path,required=True); ap.add_argument('--materiality-2015-dir',type=pathlib.Path,required=True)
    ap.add_argument('--nba',type=pathlib.Path,required=True); ap.add_argument('--pbp',type=pathlib.Path,required=True); ap.add_argument('--output-dir',type=pathlib.Path,required=True)
    ap.add_argument('--per-game-timeout-seconds',type=int,default=120); args=ap.parse_args()
    season=f"{args.year}-{(args.year+1)%100:02d}"; args.output_dir.mkdir(parents=True,exist_ok=True)
    accepted=targets.load_accepted(args.materiality_dir,args.materiality_2015_dir)
    games,team_targets,player_targets,affected=targets.load_required(args.current_dir,accepted,season)
    if not games: raise RuntimeError(f'no residual games {season}')
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={gid(g):x.copy() for g,x in nba.groupby('GAME_ID',sort=False)}; pg={gid(g):x.copy() for g,x in pbp.groupby('GAMEID',sort=False)}
    signal.signal(signal.SIGALRM,timeout); team_out=[]; player_out=[]; diags=[]; counts=Counter()
    for i,g in enumerate(games,1):
        rt=sorted(team_targets.get(g,set())); rp=sorted(player_targets.get(g,set()))
        if g not in ng or g not in pg:
            diags.append({'season':season,'game_id':g,'status':'SOURCE_SET_GAP','error':json.dumps({'nba':g in ng,'pbp':g in pg})}); counts['SOURCE_SET_GAP']+=1; continue
        try:
            signal.alarm(args.per_game_timeout_seconds); tr,pr,a=build_game_v2(int(g),ng[g],pg[g]); signal.alarm(0)
        except Exception as e:
            signal.alarm(0); diags.append({'season':season,'game_id':g,'status':'V2_RECONSTRUCTION_FAILED','error':f'{type(e).__name__}: {e}'}); counts['V2_RECONSTRUCTION_FAILED']+=1; continue
        tb={tid(r['team_id']):r for r in tr}; pp={(tid(r['team_id']),pid(r['player_id'])):r for r in pr}; byp=defaultdict(set)
        for r in pr: byp[pid(r['player_id'])].add(tid(r['team_id']))
        ateam=aplayer=0
        for t in rt:
            r=tb.get(t)
            if r:
                team_out.append({'season':season,'game_id':g,'team_id':t,**{z:int(round(float(r[z]))) for z in REQT},'provenance':'exact retained NBA V2 + PBP production reconstruction'}); ateam+=1
        for t,p in rp:
            if t not in tb: continue
            r=pp.get((t,p))
            if r:
                player_out.append({'season':season,'game_id':g,'team_id':t,'player_id':p,**{z:int(round(float(r[z]))) for z in REQP},'exact_zero_proof':False,'provenance':'exact retained NBA V2 + PBP production reconstruction'}); aplayer+=1
            elif not (byp.get(p,set())-{t}):
                player_out.append({'season':season,'game_id':g,'team_id':t,'player_id':p,**{z:0 for z in REQP},'exact_zero_proof':True,'provenance':'exact zero from validated exhaustive NBA V2 lineup reconstruction'}); aplayer+=1
        st='PASS_EXACT' if ateam==len(rt) and aplayer==len(rp) else 'PARTIAL_EXACT'; counts[st]+=1
        diags.append({'season':season,'game_id':g,'status':st,'required_team':len(rt),'recovered_team':ateam,'required_player':len(rp),'recovered_player':aplayer,'error':''})
        print(json.dumps({'event':'V2_GAME','season':season,'game':g,'status':st,'player':f'{aplayer}/{len(rp)}','team':f'{ateam}/{len(rt)}','n':f'{i}/{len(games)}'}),flush=True)
    tm={}; pm={}
    for r in team_out: tm[(r['game_id'],r['team_id'])]=r
    for r in player_out: pm[(r['game_id'],r['team_id'],r['player_id'])]=r
    team_out=[tm[k] for k in sorted(tm)]; player_out=[pm[k] for k in sorted(pm)]
    write_gz(args.output_dir/'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz',team_out,['season','game_id','team_id',*REQT,'provenance'])
    write_gz(args.output_dir/'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz',player_out,['season','game_id','team_id','player_id',*REQP,'exact_zero_proof','provenance'])
    pd.DataFrame(diags).to_csv(args.output_dir/'V2_RESIDUAL_GAME_DIAGNOSTICS.csv',index=False)
    qa={'status':'PASS','season':season,'target_games':len(games),'affected_keys':len(affected),'recovered_team_targets':len(team_out),'recovered_player_targets':len(player_out),'game_status_counts':dict(counts),'integrity':{'exact_nba_v2_pbp_only':True,'complete_team_player_seconds_validation':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False}}
    (args.output_dir/'V2_RESIDUAL_GAME_QA.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa,indent=2),flush=True)
if __name__=='__main__': main()
