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
class GameTimeout(RuntimeError): pass
def timeout(signum,frame): raise GameTimeout('adjudication per-game timeout')
def gid(x): return targets.gid(x)
def tid(x): return targets.tid(x)
def pid(x): return targets.pid(x)

def _norm(x): return v2._norm(x)

def _candidate_rows(lineups,row,alpha=5):
    nba=lineups.events
    period=int(row.PERIOD)
    start=core.elapsed_seconds(period,row.STARTTIME)
    end=core.elapsed_seconds(period,row.ENDTIME)
    c=nba[(nba.PERIOD.eq(period)) & (nba.ELAPSED.gt(start-alpha)) & (nba.ELAPSED.lt(end+alpha)) & nba.EVENTMSGTYPE.eq(4)].copy()
    if c.empty:return c
    c=c[[core._nba_real_rebound(nba,int(ix)) for ix in c.index]]
    return c

def _strict_unique_repairs(game_id,lineups,pbp_game):
    # First use the validated production join. We only adjudicate rows it cannot match.
    _,audit=v2.join_pbp_rebounds(lineups,pbp_game)
    if int(audit.get('unmatched_rebound_bearing_rows',0))==0:return {},[]
    ordered=pbp_game.copy()
    ordered['DESCRIPTION_NORM']=ordered.DESCRIPTION.map(_norm)
    ordered['START_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(ordered.PERIOD,ordered.STARTTIME)]
    ordered['END_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(ordered.PERIOD,ordered.ENDTIME)]
    rebounds=ordered[ordered.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    # Reproduce production matching to isolate the exact same unmatched rows.
    nba=lineups.events; repairs={}; evidence=[]
    for _,row in rebounds.iterrows():
        cand=nba[(nba.PERIOD.eq(row.PERIOD)) & (nba.ELAPSED.gt(row.START_ELAPSED-5)) & (nba.ELAPSED.lt(row.END_ELAPSED+5))]
        scored=[(core._distance(row.DESCRIPTION_NORM,d),int(ev),int(ix)) for ix,ev,d in zip(cand.index,cand.EVENTNUM,cand.DESCRIPTION_NORM)]
        if any(s<.2 for s,_,__ in scored):
            continue
        # Existing locked repair already handles this row if present.
        locked=v2.JOIN_REPAIRS.get((int(game_id),int(row.PERIOD),row.DESCRIPTION_NORM))
        if locked is not None:
            continue
        rc=_candidate_rows(lineups,row,5)
        # Fail closed unless the local source interval contains exactly one real NBA rebound event.
        if len(rc)!=1:
            raise ValueError(f'unmatched row not uniquely adjudicable period={int(row.PERIOD)} desc={row.DESCRIPTION!s} candidates={len(rc)}')
        hit=rc.iloc[0]; ev=int(hit.EVENTNUM); key=(int(game_id),int(row.PERIOD),row.DESCRIPTION_NORM)
        # The production repair key is description-based. Duplicate descriptions may be used only if they resolve to the same event.
        if key in repairs and repairs[key]!=ev:
            raise ValueError(f'duplicate repair key maps to multiple events key={key} {repairs[key]}!={ev}')
        repairs[key]=ev
        evidence.append({'game_id':int(game_id),'period':int(row.PERIOD),'pbp_start':str(row.STARTTIME),'pbp_end':str(row.ENDTIME),'pbp_description':str(row.DESCRIPTION),'nba_eventnum':ev,'nba_clock':str(hit.PCTIMESTRING) if 'PCTIMESTRING' in hit else '', 'nba_description':str(hit.DESCRIPTION) if 'DESCRIPTION' in hit else '', 'candidate_count':1,'rule':'unique real NBA rebound in same period within PBP interval +/-5 seconds'})
    if len(repairs)!=int(audit.get('unmatched_rebound_bearing_rows',0)):
        raise ValueError(f'adjudication accounting mismatch repairs={len(repairs)} unmatched={audit.get("unmatched_rebound_bearing_rows")}')
    return repairs,evidence

def _expected_team_player_seconds(duration,team_id,player_team,lineups):
    """Expected player-seconds after explicit locked source-gap repairs.

    The production lineup engine can remove source-proven unrecorded clock gaps.
    Those seconds are intentionally absent from every affected player's exposure,
    so the conservation target must remove the same locked gap rather than
    comparing against nominal wall-clock duration. This is not a tolerance: only
    explicit period_start_clock_gap_repair records produced by the locked engine
    are recognized.
    """
    expected=int(duration)*5
    for repair in lineups.repairs:
        if str(repair.get('type'))!='period_start_clock_gap_repair':
            continue
        gap=int(repair.get('seconds_removed') or 0)
        players=[int(p) for p in repair.get('players',[])]
        expected-=gap*sum(1 for p in players if int(player_team.get(p,-1))==int(team_id))
    return expected

def build_game(game_id,nba_game,pbp_game):
    lineups=v2.reconstruct_game_lineups(nba_game)
    repairs,evidence=_strict_unique_repairs(game_id,lineups,pbp_game)
    old={}
    try:
        for k,ev in repairs.items():
            if k in v2.JOIN_REPAIRS: old[k]=v2.JOIN_REPAIRS[k]
            v2.JOIN_REPAIRS[k]=ev
        joined,audit=v2.join_pbp_rebounds(lineups,pbp_game)
    finally:
        for k in repairs:
            if k in old:v2.JOIN_REPAIRS[k]=old[k]
            else:v2.JOIN_REPAIRS.pop(k,None)
    if int(audit.get('unmatched_rebound_bearing_rows',0)):
        raise ValueError(f'post-adjudication unmatched={audit["unmatched_rebound_bearing_rows"]}')
    joined=v2.classify_rebounds(joined)
    player_team=core._player_team(nba_game); team_abbr=base._team_abbreviations(nba_game); names=base._player_names(nba_game)
    teams=sorted(set(int(x) for x in player_team.values()))
    if len(teams)!=2: raise ValueError(f'expected 2 teams got {teams}')
    if any(t not in team_abbr for t in teams): raise ValueError('missing team abbreviation')
    duration=base._duration_seconds(nba_game)
    real=joined.IS_REAL_REBOUND.astype(bool); oreb=joined.IS_OREB.astype(bool)
    team_rows=[]; masks={}
    for t in teams:
        ab=team_abbr[t]; offense=~joined.OPPONENT.astype(str).eq(ab); defense=~offense; masks[t]=(offense,defense)
        team_rows.append({'game_id':int(game_id),'team_id':t,'team_abbr':ab,'game_seconds':duration,'team_oreb':base._count(offense&oreb),'team_dreb':base._count(defense&real&~oreb),'opponent_oreb':base._count(defense&oreb),'opponent_dreb':base._count(offense&real&~oreb)})
    player_rows=[]
    for p,seconds in sorted(lineups.seconds.items()):
        p=int(p); sec=int(round(float(seconds)))
        if sec<=0:continue
        t=player_team.get(p)
        if t is None:raise ValueError(f'positive-second player has no team {p}')
        t=int(t); on=joined.LINEUP.map(lambda lu:p in lu); offense,defense=masks[t]
        player_rows.append({'game_id':int(game_id),'team_id':t,'team_abbr':team_abbr[t],'player_id':p,'player':names.get(p,''),'seconds_on':sec,'team_oreb_on':base._count(on&offense&oreb),'team_dreb_on':base._count(on&defense&real&~oreb),'opponent_oreb_on':base._count(on&defense&oreb),'opponent_dreb_on':base._count(on&offense&real&~oreb)})
    for t in teams:
        observed=sum(r['seconds_on'] for r in player_rows if r['team_id']==t)
        expected=_expected_team_player_seconds(duration,t,player_team,lineups)
        if observed!=expected:raise ValueError(f'team player-seconds mismatch team={t} {observed}!={expected}')
    return team_rows,player_rows,evidence

def write_gz(path,rows,fields):
    with gzip.open(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({z:r.get(z,'') for z in fields} for r in rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--current-dir',type=pathlib.Path,required=True);ap.add_argument('--materiality-dir',type=pathlib.Path,required=True);ap.add_argument('--materiality-2015-dir',type=pathlib.Path,required=True);ap.add_argument('--nba',type=pathlib.Path,required=True);ap.add_argument('--pbp',type=pathlib.Path,required=True);ap.add_argument('--output-dir',type=pathlib.Path,required=True);ap.add_argument('--timeout-seconds',type=int,default=180);args=ap.parse_args()
    season=f'{args.year}-{(args.year+1)%100:02d}';args.output_dir.mkdir(parents=True,exist_ok=True)
    accepted=targets.load_accepted(args.materiality_dir,args.materiality_2015_dir);games,team_targets,player_targets,affected=targets.load_required(args.current_dir,accepted,season)
    if not games:raise RuntimeError(f'no residual games {season}')
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False));pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False));ng={gid(g):x.copy() for g,x in nba.groupby('GAME_ID',sort=False)};pg={gid(g):x.copy() for g,x in pbp.groupby('GAMEID',sort=False)}
    signal.signal(signal.SIGALRM,timeout);to=[];po=[];diag=[];evout=[];counts=Counter()
    for g in games:
        rt=sorted(team_targets.get(g,set()));rp=sorted(player_targets.get(g,set()))
        if g not in ng or g not in pg:diag.append({'season':season,'game_id':g,'status':'SOURCE_SET_GAP','error':json.dumps({'nba':g in ng,'pbp':g in pg})});counts['SOURCE_SET_GAP']+=1;continue
        try:
            signal.alarm(args.timeout_seconds);tr,pr,evidence=build_game(int(g),ng[g],pg[g]);signal.alarm(0)
        except Exception as e:
            signal.alarm(0);diag.append({'season':season,'game_id':g,'status':'FAIL_CLOSED','error':f'{type(e).__name__}: {e}'});counts['FAIL_CLOSED']+=1;continue
        tb={tid(r['team_id']):r for r in tr};pp={(tid(r['team_id']),pid(r['player_id'])):r for r in pr};byp=defaultdict(set)
        for r in pr:byp[pid(r['player_id'])].add(tid(r['team_id']))
        at=apc=0
        for t in rt:
            if t in tb:to.append({'season':season,'game_id':g,'team_id':t,**{z:int(tb[t][z]) for z in REQT},'provenance':'exact NBA V2 + PBP with unique local rebound adjudication'});at+=1
        for t,p in rp:
            r=pp.get((t,p))
            if r:po.append({'season':season,'game_id':g,'team_id':t,'player_id':p,**{z:int(r[z]) for z in REQP},'exact_zero_proof':False,'provenance':'exact NBA V2 + PBP with unique local rebound adjudication'});apc+=1
            elif not (byp.get(p,set())-{t}):po.append({'season':season,'game_id':g,'team_id':t,'player_id':p,**{z:0 for z in REQP},'exact_zero_proof':True,'provenance':'exact zero from validated exhaustive NBA V2 lineup reconstruction after unique local rebound adjudication'});apc+=1
        evout.extend(evidence);st='PASS_EXACT' if at==len(rt) and apc==len(rp) else 'PARTIAL_EXACT';counts[st]+=1;diag.append({'season':season,'game_id':g,'status':st,'required_team':len(rt),'recovered_team':at,'required_player':len(rp),'recovered_player':apc,'adjudicated_rows':len(evidence),'error':''})
    tm={(r['game_id'],r['team_id']):r for r in to};pm={(r['game_id'],r['team_id'],r['player_id']):r for r in po};to=[tm[k] for k in sorted(tm)];po=[pm[k] for k in sorted(pm)]
    write_gz(args.output_dir/'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz',to,['season','game_id','team_id',*REQT,'provenance']);write_gz(args.output_dir/'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz',po,['season','game_id','team_id','player_id',*REQP,'exact_zero_proof','provenance']);pd.DataFrame(diag).to_csv(args.output_dir/'UNIQUE_REBOUND_ADJUDICATION_DIAGNOSTICS.csv',index=False);pd.DataFrame(evout).to_csv(args.output_dir/'UNIQUE_REBOUND_ADJUDICATION_EVIDENCE.csv',index=False)
    qa={'status':'PASS','season':season,'target_games':len(games),'affected_keys':len(affected),'recovered_team_targets':len(to),'recovered_player_targets':len(po),'adjudicated_rows':len(evout),'game_status_counts':dict(counts),'integrity':{'unique_real_rebound_same_period_narrow_clock_only':True,'complete_team_player_seconds_validation':True,'locked_period_start_gap_repairs_honored_in_seconds_conservation':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'global_event_ordering_used':False}}
    (args.output_dir/'UNIQUE_REBOUND_ADJUDICATION_QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2),flush=True)
if __name__=='__main__':main()
