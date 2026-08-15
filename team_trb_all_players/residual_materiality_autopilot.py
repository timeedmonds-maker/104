#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import itertools
import json
import math
import re
from pathlib import Path

import pandas as pd

import build_exact_game_fact_layer as game_builder
import local_treb_rebuild as core
import production_rebound_v3 as rebound_v3
import production_rebound_v4 as rebound_v4
import production_treb_engine_v3 as lineup_v3
import run_local_treb_production as io

PP_LIMIT = 0.01
FRACTION_LIMIT = PP_LIMIT / 100.0
STATE_CAP = 50000
SCENARIO_CAP = 256


class NeedChoice(Exception):
    def __init__(self, key, options):
        self.key = key
        self.options = options
        super().__init__(f"starter choice required {key} n={len(options)}")


def sid(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s


def ratio(a, b):
    d = float(a) + float(b)
    if d <= 0:
        raise ValueError('nonpositive rebound denominator')
    return float(a) / d


def load_targets(path: Path, season: str) -> list[dict]:
    rows=[]
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            r=json.loads(line)
            if str(r.get('season'))==season and bool(r.get('full_core_reuse')):
                r['team_id']=int(r['team_id']); r['player_id']=sid(r['player_id']); rows.append(r)
    return rows


def load_prior_facts(root: Path, season: str):
    team=[]; player=[]
    for p in root.rglob('team_game_treb.csv.gz'):
        try:
            d=pd.read_csv(p,low_memory=False)
            if not d.empty: team.append(d)
        except Exception: pass
    for p in root.rglob('player_game_treb_on.csv.gz'):
        try:
            d=pd.read_csv(p,low_memory=False)
            if not d.empty: player.append(d)
        except Exception: pass
    td=pd.concat(team,ignore_index=True) if team else pd.DataFrame()
    pdx=pd.concat(player,ignore_index=True) if player else pd.DataFrame()
    if not td.empty:
        td=td.drop_duplicates(['game_id','team_id'],keep='first')
    if not pdx.empty:
        pdx['player_id']=pdx.player_id.map(sid)
        pdx=pdx.drop_duplicates(['game_id','team_id','player_id'],keep='first')
    return td,pdx


def starter_options(period, game_id, period_number, team_id, player_team, prior, evidence):
    key=(int(game_id),int(period_number),int(team_id))
    explicit=lineup_v3.legacy.core.STARTER_REPAIRS.get(key)
    if explicit is not None: return [set(map(int,explicit))]
    if key in evidence: return [set(map(int,evidence[key]))]
    candidates=lineup_v3._candidate_starters(period,team_id,player_team)
    pool=set(candidates)
    if prior: pool |= set(prior)
    if len(pool)<5: return []
    combos=[set(c) for c in itertools.combinations(sorted(pool),5) if candidates.issubset(c)] if len(candidates)<5 else [set(c) for c in itertools.combinations(sorted(candidates),5)]
    evaluated=[]
    for combo in combos:
        legal,viol=lineup_v3._simulate_team(period,team_id,combo,player_team)
        if legal: evaluated.append((len(viol),tuple(sorted(combo)),viol))
    if not evaluated: return []
    best_score=min(x[0] for x in evaluated)
    # Missing-transition solutions are not structural exact solutions.
    if best_score != 0: return []
    return [set(x[1]) for x in evaluated if x[0]==best_score]


def enumerate_lineup_forces(nba_game, v3_game):
    original=lineup_v3._choose_starters
    evidence=lineup_v3._load_evidence_repairs()
    completed=[]; seen=set(); capped=False

    def explore(forced):
        nonlocal capped
        if len(completed)>=SCENARIO_CAP:
            capped=True; return
        sig=tuple(sorted((k,tuple(sorted(v))) for k,v in forced.items()))
        if sig in seen: return
        seen.add(sig)

        def choose(period,game_id,period_number,team_id,player_team,prior,evidence_repairs):
            key=(int(game_id),int(period_number),int(team_id))
            if key in forced:
                chosen=set(forced[key])
                legal,viol=lineup_v3._simulate_team(period,team_id,chosen,player_team)
                if not legal or viol:
                    raise ValueError(f'forced starter not structurally exact {key}')
                return chosen,{'type':'autopilot_forced_legal_starter','starters':sorted(chosen)}
            try:
                return original(period,game_id,period_number,team_id,player_team,prior,evidence_repairs)
            except ValueError as exc:
                msg=str(exc)
                if 'non-unique v3/team-local starter solution' not in msg:
                    raise
                opts=starter_options(period,game_id,period_number,team_id,player_team,prior,evidence_repairs)
                if len(opts)<=1: raise
                raise NeedChoice(key,opts)

        lineup_v3._choose_starters=choose
        try:
            lu=lineup_v3.reconstruct_game_lineups(nba_game,v3_game)
            completed.append((dict(forced),lu))
        except NeedChoice as nc:
            for opt in nc.options:
                nxt={k:set(v) for k,v in forced.items()}; nxt[nc.key]=set(opt); explore(nxt)
                if capped: break
        except Exception:
            pass
        finally:
            lineup_v3._choose_starters=original

    explore({})
    return completed,capped


def rebound_rows(frame):
    d=frame.copy(); d['_ord']=range(len(d))
    d=d[d.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    d['_start']=[int(core.elapsed_seconds(int(p),c)) for p,c in zip(d.PERIOD,d.STARTTIME)]
    d['_end']=[int(core.elapsed_seconds(int(p),c)) for p,c in zip(d.PERIOD,d.ENDTIME)]
    return d


def enumerate_rebound_repairs(lineups,pbp_game):
    joined,audit=rebound_v3.join_pbp_rebounds(lineups,pbp_game)
    rows=rebound_rows(pbp_game); nbaev=lineups.events
    matched=set(joined.index); unmatched=sorted([idx for idx in rows.index if idx not in matched],key=lambda i:(int(rows.loc[i,'PERIOD']),int(rows.loc[i,'_ord'])))
    if not unmatched: return [[]],False
    used=set(pd.to_numeric(joined.NBA_INDEX,errors='coerce').dropna().astype(int))
    reb_indices=[int(i) for i in nbaev.index[pd.to_numeric(nbaev.EVENTMSGTYPE,errors='coerce').eq(4)]]
    def cand(idx,used_now):
        r=rows.loc[idx]; lo=min(int(r['_start']),int(r['_end']))-5; hi=max(int(r['_start']),int(r['_end']))+5; per=int(r.PERIOD)
        out=[]
        for ni in reb_indices:
            if ni in used_now: continue
            n=nbaev.loc[ni]
            if int(n.PERIOD)==per and lo<=int(n.ELAPSED)<=hi: out.append(ni)
        return sorted(out,key=lambda ni:(int(nbaev.loc[ni,'ELAPSED']),int(nbaev.loc[ni,'EVENTNUM'])))
    sols=[]; capped=False
    def walk(pos,chosen,local,last_ev):
        nonlocal capped
        if len(sols)>=SCENARIO_CAP: capped=True; return
        if pos==len(unmatched): sols.append(chosen.copy()); return
        idx=unmatched[pos]
        for ni in cand(idx,used|local):
            ev=int(nbaev.loc[ni,'EVENTNUM'])
            if ev<=last_ev: continue
            chosen.append((idx,ni)); local.add(ni); walk(pos+1,chosen,local,ev); local.remove(ni); chosen.pop()
            if capped: return
    walk(0,[],set(),-1)
    recsets=[]
    for sol in sols:
        recs=[]
        for idx,ni in sol:
            r=rows.loc[idx]; n=nbaev.loc[ni]
            recs.append({'game_id':int(pbp_game.GAMEID.iloc[0]),'period':int(r.PERIOD),'start_time':str(r.STARTTIME),'end_time':str(r.ENDTIME),'pbp_description':str(r.DESCRIPTION),'nba_eventnum':int(n.EVENTNUM),'nba_elapsed':int(n.ELAPSED),'lineup':[int(x) for x in n.LINEUP],'real':bool(core._nba_real_rebound(nbaev,ni)),'method':'autopilot_legal_order_preserving_scenario'})
        recsets.append(recs)
    return recsets,capped


def build_with_forces(nba_game,v3_game,pbp_game,forces,recs):
    orig_choose=lineup_v3._choose_starters; orig_load=rebound_v4._load_repairs
    existing=orig_load()
    merged={k:list(v) for k,v in existing.items()}
    if recs:
        gid=int(pbp_game.GAMEID.iloc[0]); merged[gid]=list(merged.get(gid,[]))+list(recs)
    evidence=lineup_v3._load_evidence_repairs()
    def choose(period,game_id,period_number,team_id,player_team,prior,evidence_repairs):
        key=(int(game_id),int(period_number),int(team_id))
        if key in forces:
            chosen=set(forces[key]); legal,viol=lineup_v3._simulate_team(period,team_id,chosen,player_team)
            if not legal or viol: raise ValueError(f'forced starter invalid {key}')
            return chosen,{'type':'autopilot_forced_legal_starter','starters':sorted(chosen)}
        return orig_choose(period,game_id,period_number,team_id,player_team,prior,evidence_repairs)
    lineup_v3._choose_starters=choose; rebound_v4._load_repairs=lambda: merged
    try:
        return game_builder.build_game(int(pbp_game.GAMEID.iloc[0]),nba_game,v3_game,pbp_game)
    finally:
        lineup_v3._choose_starters=orig_choose; rebound_v4._load_repairs=orig_load


def game_variants(gid,ng,vg,pg,candidate_map):
    if gid not in ng or gid not in vg or gid not in pg:
        return [],{'status':'SOURCE_SET_GAP','nba':gid in ng,'v3':gid in vg,'pbp':gid in pg}
    # Existing production exact path first.
    try:
        tr,pr,a=game_builder.build_game(gid,ng[gid],vg[gid],pg[gid]); return [(tr,pr)],{'status':'BASE_EXACT','variants':1}
    except Exception as first_exc:
        first=str(first_exc)
    # Runtime-assert the finite new candidate repairs first.
    if gid in candidate_map:
        try:
            tr,pr,a=build_with_forces(ng[gid],vg[gid],pg[gid],{},candidate_map[gid]); return [(tr,pr)],{'status':'CANDIDATE_REPLAY_EXACT','variants':1,'repairs':len(candidate_map[gid])}
        except Exception as exc:
            candidate_error=f'{type(exc).__name__}: {exc}'
    else: candidate_error=''

    lineup_scenarios,capped_lu=enumerate_lineup_forces(ng[gid],vg[gid])
    if not lineup_scenarios:
        # If lineups are already unique, use that one scenario for rebound enumeration.
        try:
            lu=lineup_v3.reconstruct_game_lineups(ng[gid],vg[gid]); lineup_scenarios=[({},lu)]
        except Exception:
            return [],{'status':'NO_LEGAL_LINEUP_SCENARIO','error':first,'candidate_error':candidate_error}
    variants=[]; rebound_capped=False
    for forces,lu in lineup_scenarios:
        recsets,cap=enumerate_rebound_repairs(lu,pg[gid]); rebound_capped |= cap
        for recs in recsets:
            try:
                tr,pr,a=build_with_forces(ng[gid],vg[gid],pg[gid],forces,recs); variants.append((tr,pr))
            except Exception:
                continue
            if len(variants)>=SCENARIO_CAP: rebound_capped=True; break
        if len(variants)>=SCENARIO_CAP: break
    # Deduplicate exact fact variants.
    unique=[]; seen=set()
    for tr,pr in variants:
        key=json.dumps({'t':sorted(tr,key=lambda r:r['team_id']),'p':sorted(pr,key=lambda r:(r['team_id'],r['player_id']))},sort_keys=True,default=str)
        if key not in seen: seen.add(key); unique.append((tr,pr))
    if not unique:
        return [],{'status':'NO_COMPLETE_EXACT_SCENARIO','error':first,'candidate_error':candidate_error,'lineup_scenarios':len(lineup_scenarios)}
    return unique,{'status':'LEGAL_SCENARIOS','variants':len(unique),'lineup_capped':capped_lu,'rebound_capped':rebound_capped}


def contribution(variant,team_id,pid):
    tr,pr=variant
    team=next((r for r in tr if int(r['team_id'])==team_id),None)
    if team is None: return None
    player=next((r for r in pr if int(r['team_id'])==team_id and sid(r['player_id'])==pid),None)
    if player is None:
        player={'seconds_on':0,'team_oreb_on':0,'team_dreb_on':0,'opponent_oreb_on':0,'opponent_dreb_on':0}
    return (int(player['seconds_on']),int(player['team_oreb_on']),int(player['team_dreb_on']),int(player['opponent_oreb_on']),int(player['opponent_dreb_on']),int(team['game_seconds']),int(team['team_oreb']),int(team['team_dreb']),int(team['opponent_oreb']),int(team['opponent_dreb']))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--year',type=int,required=True); ap.add_argument('--targets',type=Path,required=True)
    ap.add_argument('--historical-dir',type=Path,required=True); ap.add_argument('--report',type=Path,required=True); ap.add_argument('--candidates',type=Path,required=True)
    ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    season=f'{args.year}-{(args.year+1)%100:02d}'
    targets=load_targets(args.targets,season); target_keys={(int(r['team_id']),sid(r['player_id'])):r for r in targets}
    report=[r for r in json.loads(args.report.read_text()) if str(r.get('season'))==season]
    cand=json.loads(args.candidates.read_text()).get('repairs',[]); candidate_map={}
    for r in cand: candidate_map.setdefault(int(r['game_id']),[]).append(r)
    gids=sorted({int(r['game_id']) for r in report})
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); v3=lineup_v3.normalize_v3(pd.read_csv(args.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba[pd.to_numeric(nba.GAME_ID,errors='coerce').isin(gids)].groupby('GAME_ID',sort=False)}
    vg={int(g):f.copy() for g,f in v3[pd.to_numeric(v3.gameId,errors='coerce').isin(gids)].groupby('gameId',sort=False)}
    pg={int(g):f.copy() for g,f in pbp[pd.to_numeric(pbp.GAMEID,errors='coerce').isin(gids)].groupby('GAMEID',sort=False)}
    variants={}; game_qa={}
    for gid in gids:
        variants[gid],game_qa[gid]=game_variants(gid,ng,vg,pg,candidate_map)
        print(json.dumps({'event':'GAME_SCENARIO', 'season':season,'game_id':gid,**game_qa[gid]}),flush=True)
    prior_team,prior_player=load_prior_facts(args.historical_dir,season)
    failed_by_team={}
    for r in report:
        gid=int(r['game_id'])
        for t in r.get('teams',[]) or []: failed_by_team.setdefault(int(t),set()).add(gid)
    # If report lacks teams for a game, derive from any variant or NBA participants.
    for gid in gids:
        teams=set()
        if variants.get(gid): teams={int(r['team_id']) for r in variants[gid][0][0]}
        elif gid in ng:
            for n in (1,2,3):
                c=f'PLAYER{n}_TEAM_ID'
                if c in ng[gid]: teams.update(int(x) for x in pd.to_numeric(ng[gid][c],errors='coerce').dropna() if int(x)>0)
        for t in teams: failed_by_team.setdefault(t,set()).add(gid)

    out_rows=[]; blockers=[]
    for (tid,pid),t in sorted(target_keys.items()):
        bad=sorted(failed_by_team.get(tid,set()))
        missing=[g for g in bad if not variants.get(g)]
        if missing:
            blockers.append({'season':season,'team_id':tid,'player_id':pid,'status':'UNRESOLVED_GAME_SCENARIO','games':','.join(map(str,missing))}); continue
        bt=prior_team[prior_team.team_id.astype(int).eq(tid)] if not prior_team.empty else pd.DataFrame()
        bp=prior_player[(prior_player.team_id.astype(int).eq(tid)) & (prior_player.player_id.astype(str).eq(pid))] if not prior_player.empty else pd.DataFrame()
        # Remove failed games in case any partial artifact accidentally contains one.
        if not bt.empty: bt=bt[~bt.game_id.astype(int).isin(bad)]
        if not bp.empty: bp=bp[~bp.game_id.astype(int).isin(bad)]
        base=(int(bp.seconds_on.sum()) if not bp.empty else 0,int(bp.team_oreb_on.sum()) if not bp.empty else 0,int(bp.team_dreb_on.sum()) if not bp.empty else 0,int(bp.opponent_oreb_on.sum()) if not bp.empty else 0,int(bp.opponent_dreb_on.sum()) if not bp.empty else 0,int(bt.game_seconds.sum()) if not bt.empty else 0,int(bt.team_oreb.sum()) if not bt.empty else 0,int(bt.team_dreb.sum()) if not bt.empty else 0,int(bt.opponent_oreb.sum()) if not bt.empty else 0,int(bt.opponent_dreb.sum()) if not bt.empty else 0)
        states={base}
        capped=False
        for gid in bad:
            cs={contribution(v,tid,pid) for v in variants[gid]}; cs.discard(None)
            if not cs: states=set(); break
            nxt=set()
            for a in states:
                for b in cs:
                    nxt.add(tuple(x+y for x,y in zip(a,b)))
                    if len(nxt)>STATE_CAP: capped=True; break
                if capped: break
            states=nxt
            if capped: break
        if capped or not states:
            blockers.append({'season':season,'team_id':tid,'player_id':pid,'status':'SCENARIO_STATE_CAP' if capped else 'NO_SCENARIO_STATE','games':','.join(map(str,bad))}); continue
        target_seconds=float(t.get('seconds_on',0.0))
        minute_diffs=[abs(s[0]-target_seconds) for s in states]
        if any(x>60.0+1e-9 for x in minute_diffs):
            blockers.append({'season':season,'team_id':tid,'player_id':pid,'status':'MINUTES_SCENARIO_GATE','max_seconds_diff':max(minute_diffs),'games':','.join(map(str,bad))}); continue
        vals=[]
        for s in states:
            sec_on,to,tdr,oo,od,game_sec,tto,ttd,oto,otd=s
            team_on=to+tdr; opp_on=oo+od; team_total=tto+ttd; opp_total=oto+otd
            on=ratio(team_on,opp_on); off=ratio(team_total-team_on,opp_total-opp_on); vals.append((on,off,on-off,s))
        ranges=[(max(v[i] for v in vals)-min(v[i] for v in vals))*100.0 for i in range(3)]
        canonical=min(vals,key=lambda x:x[3])
        on,off,sw,s=canonical; sec_on,to,tdr,oo,od,game_sec,tto,ttd,oto,otd=s
        exact=len(states)==1
        material=all(r<=PP_LIMIT+1e-12 for r in ranges)
        if not exact and not material:
            blockers.append({'season':season,'team_id':tid,'player_id':pid,'status':'MATERIAL_VARIANCE','on_range_pp':ranges[0],'off_range_pp':ranges[1],'swing_range_pp':ranges[2],'scenario_states':len(states),'games':','.join(map(str,bad))}); continue
        out_rows.append({'season':season,'team_id':tid,'player_id':pid,'player':t.get('player',''),'target_minutes_on':target_seconds/60.0,'direct_treb_on':on,'direct_treb_off':off,'direct_minutes_on':sec_on/60.0,'direct_minutes_off':(game_sec-sec_on)/60.0,'team_oreb_on':to,'team_dreb_on':tdr,'opponent_oreb_on':oo,'opponent_dreb_on':od,'team_oreb_off':tto-to,'team_dreb_off':ttd-tdr,'opponent_oreb_off':oto-oo,'opponent_dreb_off':otd-od,'status':'PASS','error':'','source':'autopilot_exact_legal_scenario' if exact else 'autopilot_materiality_legal_scenarios','scenario_states':len(states),'materiality_on_range_pp':ranges[0],'materiality_off_range_pp':ranges[1],'materiality_swing_range_pp':ranges[2],'materiality_threshold_pp':PP_LIMIT,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'whole_team_subtraction_across_partial_tenure_used':False})
    pd.DataFrame(out_rows).to_csv(args.out/f'fresh_fullcore_{args.year}.csv',index=False)
    pd.DataFrame(blockers).to_csv(args.out/f'blockers_{args.year}.csv',index=False)
    qa={'status':'PASS' if len(out_rows)==len(targets) else 'INCOMPLETE','season':season,'target_keys':len(targets),'recovered_keys':len(out_rows),'unresolved_keys':len(blockers),'exact_keys':sum(r['source']=='autopilot_exact_legal_scenario' for r in out_rows),'materiality_keys':sum(r['source']=='autopilot_materiality_legal_scenarios' for r in out_rows),'materiality_threshold_pp':PP_LIMIT,'game_status_counts':dict(pd.Series([v['status'] for v in game_qa.values()]).value_counts()),'scenario_caps':sum(bool(v.get('lineup_capped') or v.get('rebound_capped')) for v in game_qa.values()),'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'whole_team_subtraction_across_partial_tenure_used':False}
    (args.out/'AUTOPILOT_QA.json').write_text(json.dumps(qa,indent=2)+'\n'); (args.out/'GAME_SCENARIO_QA.json').write_text(json.dumps(game_qa,indent=2)+'\n')
    print(json.dumps(qa,indent=2),flush=True)

if __name__=='__main__': main()
