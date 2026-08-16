#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,json,pathlib,collections
from itertools import combinations
import pandas as pd

import build_exact_game_fact_layer as base
import production_treb_engine_v3 as le
import run_local_treb_production as io
import local_treb_rebuild as core

REQP=("seconds_on","team_oreb_on","team_dreb_on","opponent_oreb_on","opponent_dreb_on")
REQT=("team_oreb","team_dreb","opponent_oreb","opponent_dreb")
TOL=1.0

def pid(x):
    s=str(x).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s

def tid(x): return str(int(float(x)))
def gid(x):
    s=str(x).strip().removesuffix('.0')
    return str(int(float(s))).zfill(10)

def load_ledger(path, wanted_games):
    seconds={}; roster=collections.defaultdict(set)
    with gzip.open(path,'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            g=gid(r['game_id'])
            if g not in wanted_games: continue
            t=tid(r['team_id']); p=pid(r['player_id']); k=(g,t,p)
            seconds[k]=float(r.get('seconds_game') or 0); roster[(g,t)].add(p)
    return seconds,roster

def load_exact_player_fact_dirs(dirs):
    facts=collections.defaultdict(dict)
    patterns=(
      'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv',
      'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz',
      'RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz',
      'RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz',
      'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv',
      'RECOVERED_CURRENT_EXACT_PLAYER_GAME_PRIMITIVES.csv',
    )
    for root in dirs:
        root=pathlib.Path(root)
        for pat in patterns:
            for pth in root.rglob(pat):
                op=gzip.open if pth.suffix=='.gz' else open
                with op(pth,'rt' if pth.suffix=='.gz' else 'r',encoding='utf-8',newline='') as f:
                    for r in csv.DictReader(f):
                        if pat=='PROMOTABLE_RETAINED_FACT_CONSENSUS.csv':
                            field=r.get('field','')
                            if field not in REQP or not str(r.get('player_id','')).strip(): continue
                            k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
                            v=float(r['value']); old=facts[k].get(field)
                            if old is not None and abs(old-v)>1e-9: raise RuntimeError(f'exact fact conflict {k} {field}')
                            facts[k][field]=v
                        else:
                            if not all(z in r and str(r[z]).strip()!='' for z in REQP): continue
                            k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
                            for z in REQP:
                                v=float(r[z]); old=facts[k].get(z)
                                if old is not None and abs(old-v)>1e-9: raise RuntimeError(f'exact fact conflict {k} {z}')
                                facts[k][z]=v
    return {k:{z:v[z] for z in REQP} for k,v in facts.items() if all(z in v for z in REQP)}

def period_seconds_and_end(period, team_id, starters, player_team, period_number):
    lineup=set(int(x) for x in starters); sec=collections.Counter()
    period_start=(period_number-1)*720 if period_number<=4 else 2880+(period_number-5)*300
    period_end=period_start+(720 if period_number<=4 else 300)
    last=period_start
    for _,event in period.iterrows():
        now=int(event.ELAPSED)
        if now>last:
            dt=now-last
            for p in lineup: sec[p]+=dt
            last=now
        if int(event.EVENTMSGTYPE)==8:
            st=le._sub_team(event,player_team,{int(team_id):lineup})
            if int(st)!=int(team_id): continue
            outgoing=int(event.PLAYER1_ID or 0); incoming=int(event.PLAYER2_ID or 0)
            if outgoing not in lineup or incoming in lineup: raise ValueError('illegal substitution during path simulation')
            lineup.remove(outgoing); lineup.add(incoming)
    if period_end>last:
        dt=period_end-last
        for p in lineup: sec[p]+=dt
    return dict(sec),frozenset(lineup)

def legal_starters(period, game_id, period_number, team_id, player_team, prior, evidence):
    key=(int(game_id),int(period_number),int(team_id))
    explicit=le.legacy.core.STARTER_REPAIRS.get(key)
    if explicit is not None: return [tuple(sorted(int(x) for x in explicit))]
    if key in evidence: return [tuple(sorted(int(x) for x in evidence[key]))]
    candidates=le._candidate_starters(period,int(team_id),player_team)
    pool=set(candidates)|(set(prior) if prior else set())
    if len(pool)<5: return []
    combos=[set(c) for c in combinations(sorted(pool),5) if candidates.issubset(c)] if len(candidates)<5 else [set(c) for c in combinations(sorted(candidates),5)]
    good=[]
    for combo in combos:
        legal,viol=le._simulate_team(period,int(team_id),combo,player_team)
        if legal and len(viol)==0: good.append(tuple(sorted(combo)))
    return sorted(set(good))

def solve_team_paths(nba_game,v3_game,game_id,team_id,ledger_seconds,max_states=200000,max_paths=5000):
    prepared,_=le.legacy.prepare_nba_game(nba_game)
    prepared=prepared.copy(); prepared['DESCRIPTION_NORM']=core.nba_description(prepared)
    prepared['ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(prepared.PERIOD,prepared.PCTIMESTRING)]
    om=le._v3_action_map(v3_game); prepared['V3_ORDER']=[om.get((int(p),int(ev)),10_000_000+int(ev)) for p,ev in zip(prepared.PERIOD,prepared.EVENTNUM)]
    player_team=core._player_team(prepared); evidence=le._load_evidence_repairs()
    team_players=sorted(int(p) for p,t in player_team.items() if int(t)==int(team_id))
    targets={p:float(ledger_seconds.get(pid(p),0.0)) for p in team_players}
    # Include ledger-only roster players; if they never appear in PBP they can only be DNP here.
    states=[(None,{}, {})]  # prior, cumulative seconds, choices
    capped=False
    for pn,period in prepared.groupby('PERIOD',sort=True):
        pn=int(pn); period=period.sort_values(['ELAPSED','V3_ORDER','EVENTNUM'],kind='stable').copy(); nxt={}
        for prior,cum,choices in states:
            for starters in legal_starters(period,int(game_id),pn,int(team_id),player_team,prior,evidence):
                try: add,end=period_seconds_and_end(period,int(team_id),starters,player_team,pn)
                except Exception: continue
                nc=dict(cum); impossible=False
                for p,s in add.items():
                    nc[p]=nc.get(p,0)+int(s)
                    target=targets.get(p,0.0)
                    if nc[p]>target+TOL: impossible=True; break
                if impossible: continue
                nch=dict(choices); nch[(int(game_id),pn,int(team_id))]=tuple(starters)
                sig=(tuple(sorted(end)),tuple(sorted((int(p),int(s)) for p,s in nc.items())))
                nxt.setdefault(sig,(end,nc,nch))
                if len(nxt)>max_states:
                    capped=True; break
            if capped: break
        states=list(nxt.values())
        if capped or not states: break
    valid=[]
    if not capped:
        allp=set(targets)
        for _,cum,choices in states:
            if all(abs(float(cum.get(p,0))-float(targets.get(p,0)))<=TOL for p in allp): valid.append(choices)
    # Deduplicate starter assignment maps.
    uniq={tuple(sorted((k,tuple(v)) for k,v in ch.items())):ch for ch in valid}
    valid=list(uniq.values())
    if len(valid)>max_paths: capped=True; valid=[]
    return valid,{'states_final':len(states),'valid_paths':len(valid),'capped':capped,'team_players':len(team_players)}

def selected_build(game_id,nba_game,v3_game,pbp_game,choices):
    old=le._choose_starters
    def choose(period,game_id_,period_number,team_id,player_team,prior,evidence_repairs):
        k=(int(game_id_),int(period_number),int(team_id))
        if k in choices:
            st=set(int(x) for x in choices[k]); return st,{'type':'ledger_constrained_exact_starter_path','team_id':int(team_id),'period':int(period_number),'starters':sorted(st)}
        return old(period,game_id_,period_number,team_id,player_team,prior,evidence_repairs)
    le._choose_starters=choose
    try: return base.build_game(int(game_id),nba_game,v3_game,pbp_game)
    finally: le._choose_starters=old

def controls_ok(game_id,player_rows,exact):
    checked=0
    by={(gid(r['game_id']),tid(r['team_id']),pid(r['player_id'])):r for r in player_rows}
    for k,v in exact.items():
        if k[0]!=gid(game_id): continue
        r=by.get(k)
        if r is None: return False,checked
        checked+=1
        if any(abs(float(r[z])-float(v[z]))>1e-9 for z in REQP): return False,checked
    return True,checked

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--current-dir',required=True); ap.add_argument('--ledger',required=True)
    ap.add_argument('--nba',required=True); ap.add_argument('--v3',required=True); ap.add_argument('--pbp',required=True); ap.add_argument('--exact-dir',action='append',default=[]); ap.add_argument('--output-dir',required=True)
    args=ap.parse_args(); y=args.year; season=f'{y}-{(y+1)%100:02d}'; out=pathlib.Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    cur=pathlib.Path(args.current_dir); needp=collections.defaultdict(set); needt=collections.defaultdict(set)
    prefix=f'002{str(y)[-2:]}'
    for r in csv.DictReader(open(next(cur.rglob('MISSING_PLAYER_GAME_PRIMITIVES.csv')),newline='')):
        g=gid(r['game_id']);
        if g.startswith(prefix): needp[g].add((tid(r['team_id']),pid(r['player_id'])))
    for r in csv.DictReader(open(next(cur.rglob('MISSING_TEAM_GAME_FACTS.csv')),newline='')):
        g=gid(r['game_id']);
        if g.startswith(prefix): needt[g].add(tid(r['team_id']))
    games=set(needp)|set(needt)
    if not games:
        (out/f'LEDGER_QA_{y}.json').write_text(json.dumps({'season':season,'status':'PASS_EMPTY','games':0},indent=2)+'\n'); return
    ledger,_=load_ledger(args.ledger,games); exact=load_exact_player_fact_dirs(args.exact_dir)
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); v3=le.normalize_v3(pd.read_csv(args.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    recovered_p=[]; recovered_t=[]; audit=[]
    for g in sorted(games):
        if g not in ng or g not in vg or g not in pg:
            audit.append({'season':season,'game_id':g,'status':'SOURCE_SET_GAP','nba':g in ng,'v3':g in vg,'pbp':g in pg}); continue
        pteam=core._player_team(ng[g]); teams=sorted(set(int(t) for t in pteam.values()))
        pathmap={}; meta={}; failed=False
        for t in teams:
            ls={pid_:sec for (gg,tt,pid_),sec in ledger.items() if gg==g and tt==tid(t)}
            paths,m=solve_team_paths(ng[g],vg[g],int(g),t,ls); pathmap[tid(t)]=paths; meta[tid(t)]=m
            if not paths: failed=True
        if failed:
            audit.append({'season':season,'game_id':g,'status':'NO_LEDGER_COMPATIBLE_PATH','meta':json.dumps(meta,sort_keys=True)}); continue
        fixed={t:paths[0] for t,paths in pathmap.items()}
        # Evaluate each team's paths independently while holding opponent to one exact-ledger-compatible path.
        team_valid_rows={}; team_valid_teamrows={}; control_checks=0
        for t,paths in pathmap.items():
            vals=[]; trows=[]
            for ch in paths:
                merged={}
                for ot,fp in fixed.items(): merged.update(ch if ot==t else fp)
                try: tr,pr,_=selected_build(g,ng[g],vg[g],pg[g],merged)
                except Exception: continue
                ok,nc=controls_ok(g,pr,exact); control_checks=max(control_checks,nc)
                if not ok: continue
                vals.append(pr); trows.append(tr)
            team_valid_rows[t]=vals; team_valid_teamrows[t]=trows
        promoted_p=promoted_t=0
        for t,p in sorted(needp.get(g,set())):
            vals=[]; complete=True
            for pr in team_valid_rows.get(t,[]):
                same=[r for r in pr if tid(r['team_id'])==t and pid(r['player_id'])==p]
                if len(same)>1: complete=False; break
                vals.append(tuple(float(same[0][z]) for z in REQP) if same else (0.,0.,0.,0.,0.))
            if not vals or not complete: continue
            if len(set(vals))==1:
                x=vals[0]; recovered_p.append({'season':season,'game_id':g,'team_id':t,'player_id':p,**{REQP[i]:x[i] for i in range(5)},'provenance':'ledger-constrained exact starter paths; all exact-ledger-compatible, retained-fact-compatible paths invariant'}); promoted_p+=1
        for t in sorted(needt.get(g,set())):
            vals=[]
            for tr in team_valid_teamrows.get(t,[]):
                same=[r for r in tr if tid(r['team_id'])==t]
                if len(same)!=1: vals=[]; break
                vals.append(tuple(float(same[0][z]) for z in REQT))
            if vals and len(set(vals))==1:
                x=vals[0]; recovered_t.append({'season':season,'game_id':g,'team_id':t,**{REQT[i]:x[i] for i in range(4)},'provenance':'ledger-constrained exact starter paths; rebound totals invariant'}); promoted_t+=1
        audit.append({'season':season,'game_id':g,'status':'EVALUATED','team_path_counts':json.dumps({t:len(v) for t,v in pathmap.items()},sort_keys=True),'control_exact_players_checked':control_checks,'player_targets':len(needp.get(g,set())),'player_promoted':promoted_p,'team_targets':len(needt.get(g,set())),'team_promoted':promoted_t,'meta':json.dumps(meta,sort_keys=True)})
        print(json.dumps({'event':'LEDGER_GAME','season':season,'game':g,'paths':{t:len(v) for t,v in pathmap.items()},'controls':control_checks,'pp':promoted_p,'tp':promoted_t}),flush=True)
    def write(path,rows,cols):
        with open(path,'w',newline='') as f: w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)
    write(out/f'LEDGER_EXACT_PLAYER_{y}.csv',recovered_p,['season','game_id','team_id','player_id',*REQP,'provenance']); write(out/f'LEDGER_EXACT_TEAM_{y}.csv',recovered_t,['season','game_id','team_id',*REQT,'provenance'])
    cols=sorted(set().union(*(r.keys() for r in audit))) if audit else ['season','game_id','status']; write(out/f'LEDGER_AUDIT_{y}.csv',audit,cols)
    qa={'season':season,'status':'PASS','games':len(games),'exact_player_promoted':len(recovered_p),'exact_team_promoted':len(recovered_t),'source_gaps':sum(r['status']=='SOURCE_SET_GAP' for r in audit),'no_ledger_path':sum(r['status']=='NO_LEDGER_COMPATIBLE_PATH' for r in audit),'integrity':{'exact_game_ledger_seconds_constraint':True,'same_game_retained_exact_player_controls':True,'model_used':False,'rounded_rate_backsolve_used':False,'opponent_inference_used':False,'unsafe_global_event_ordering_used':False}}
    (out/f'LEDGER_QA_{y}.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa),flush=True)

if __name__=='__main__': main()
