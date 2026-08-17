#!/usr/bin/env python3
"""Recover exact team-game TREB totals without reconstructing player lineups.

PBP Stats remains the rebound universe. NBA V2 is used only to establish whether
an individual PBP rebound-bearing row is a real rebound. A row that cannot be
fuzzy/locked matched is accepted only when every NBA rebound event in the same
narrow clock window has the same real/non-real status. Event identity and lineup
position are deliberately irrelevant to this team-total-only lane.

Promotion is NOT decided here. This script emits controls and target candidates;
the workflow-level global control gate must show zero exact-control mismatches
before target candidates can enter authoritative reclosure.
"""
from __future__ import annotations
import argparse,csv,gzip,json,pathlib
from collections import defaultdict,Counter
import pandas as pd
import local_treb_rebuild as core
import production_treb_engine as v2
import run_local_treb_production as io
import build_exact_game_fact_layer as base
import recover_residual_shared_games as residual

REQT=("team_oreb","team_dreb","opponent_oreb","opponent_dreb")

def gid(x): return residual.gid(x)
def tid(x): return residual.tid(x)

def find_one(root,pattern):
    xs=list(pathlib.Path(root).rglob(pattern))
    if len(xs)!=1: raise RuntimeError(f"expected one {pattern}, got {len(xs)} under {root}")
    return xs[0]

def prepare_nba(game):
    game,_=v2.prepare_nba_game(game)
    game=game.sort_values(["PERIOD","EVENTNUM"],kind="stable").reset_index(drop=True).copy()
    game["DESCRIPTION_NORM"]=core.nba_description(game)
    game["ELAPSED"]=[core.elapsed_seconds(int(p),c) for p,c in zip(game.PERIOD,game.PCTIMESTRING)]
    return game

def row_real_statuses(nba,row,alpha=5):
    period=int(row.PERIOD)
    start=core.elapsed_seconds(period,row.STARTTIME); end=core.elapsed_seconds(period,row.ENDTIME)
    cand=nba[(nba.PERIOD.eq(period)) & (nba.ELAPSED.gt(start-alpha)) & (nba.ELAPSED.lt(end+alpha))]
    scored=[(core._distance(str(row.DESCRIPTION_NORM),str(desc)),int(ev),int(ix)) for ix,ev,desc in zip(cand.index,cand.EVENTNUM,cand.DESCRIPTION_NORM)]
    acceptable=[x for x in scored if x[0] < .2]
    if acceptable:
        _,_,ix=min(acceptable)
        return bool(core._nba_real_rebound(nba,ix)),"fuzzy_exact_rule",len(acceptable)
    game_id=int(nba.GAME_ID.iloc[0])
    locked=v2.JOIN_REPAIRS.get((game_id,period,str(row.DESCRIPTION_NORM)))
    if locked is not None:
        hit=nba[(nba.PERIOD.eq(period)) & nba.EVENTNUM.eq(int(locked))]
        if len(hit)!=1: raise ValueError(f"locked repair source missing {game_id} {period} {locked}")
        ix=int(hit.index[0]); return bool(core._nba_real_rebound(nba,ix)),"locked_join_repair",1
    rb=cand[cand.EVENTMSGTYPE.eq(4)]
    if rb.empty:
        raise ValueError(f"no NBA rebound candidate period={period} desc={row.DESCRIPTION!s}")
    vals=[bool(core._nba_real_rebound(nba,int(ix))) for ix in rb.index]
    if len(set(vals))!=1:
        raise ValueError(f"mixed real-status NBA rebound candidates period={period} desc={row.DESCRIPTION!s} statuses={vals}")
    return vals[0],"invariant_real_status_narrow_clock",len(vals)

def build_team_rows(game_id,nba_game,pbp_game):
    nba=prepare_nba(nba_game)
    ordered=pbp_game.copy()
    ordered["PREV_PBP_DESCRIPTION"]=ordered.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    rebounds=ordered[ordered.DESCRIPTION.fillna("").str.contains("rebound",case=False)].copy()
    rebounds["DESCRIPTION_NORM"]=rebounds.DESCRIPTION.map(core.normalize_description)
    statuses=[]; evidence=[]
    for ix,row in rebounds.iterrows():
        real,rule,n=row_real_statuses(nba,row)
        statuses.append(real)
        evidence.append({"game_id":gid(game_id),"period":int(row.PERIOD),"description":str(row.DESCRIPTION),"rule":rule,"candidate_count":int(n),"real_rebound":bool(real)})
    rebounds["NBA_IS_REAL_REBOUND"]=statuses
    classified=core.classify_rebounds(rebounds)
    player_team=core._player_team(nba_game); teams=sorted(set(int(x) for x in player_team.values())); abbr=base._team_abbreviations(nba_game)
    if len(teams)!=2 or any(t not in abbr for t in teams): raise ValueError(f"team identity failure {teams} {abbr}")
    real=classified.IS_REAL_REBOUND.astype(bool); oreb=classified.IS_OREB.astype(bool)
    rows=[]
    for t in teams:
        a=abbr[t]; offense=~classified.OPPONENT.astype(str).eq(a); defense=~offense
        rows.append({"game_id":gid(game_id),"team_id":str(t),
                     "team_oreb":int((offense&oreb).sum()),
                     "team_dreb":int((defense&real&~oreb).sum()),
                     "opponent_oreb":int((defense&oreb).sum()),
                     "opponent_dreb":int((offense&real&~oreb).sum())})
    return rows,evidence

def load_controls(current_dir,pbp_dir,season):
    controls={}
    # Cumulative exact shared facts are authoritative exact controls.
    p=find_one(current_dir,"RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz")
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("season"))!=season: continue
            k=(gid(r["game_id"]),tid(r["team_id"])); v=tuple(int(float(r[z])) for z in REQT)
            if k in controls and controls[k]!=v: raise RuntimeError(f"control conflict {k}")
            controls[k]=v
    # Earlier validated V2+PBP exact team facts add independent control density.
    p=find_one(pbp_dir,"TREB_143_V2_PBP_EXACT_AUDIT.csv")
    with open(p,newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("season"))!=season or str(r.get("status"))!="PASS_EXACT" or str(r.get("validation_pass")).lower() not in {"true","1"}: continue
            k=(gid(r["game_id"]),tid(r["team_id"])); v=tuple(int(float(r[z])) for z in REQT)
            if k in controls and controls[k]!=v: raise RuntimeError(f"control conflict {k}")
            controls[k]=v
    return controls

def load_targets(current_dir,season):
    p=find_one(current_dir,"NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv")
    out=defaultdict(set)
    with open(p,newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("season"))!=season or int(float(r.get("team_target_count") or 0))<=0: continue
            g=gid(r["game_id"])
            for x in str(r.get("team_ids") or "").split("|"):
                if x.strip(): out[g].add(tid(x))
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,required=True);ap.add_argument("--current-dir",type=pathlib.Path,required=True);ap.add_argument("--pbp-dir",type=pathlib.Path,required=True);ap.add_argument("--nba",type=pathlib.Path,required=True);ap.add_argument("--pbp",type=pathlib.Path,required=True);ap.add_argument("--output-dir",type=pathlib.Path,required=True);args=ap.parse_args()
    season=f"{args.year}-{(args.year+1)%100:02d}"; args.output_dir.mkdir(parents=True,exist_ok=True)
    controls=load_controls(args.current_dir,args.pbp_dir,season); targets=load_targets(args.current_dir,season)
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    games=set(g for g,_ in controls)|set(targets)
    ng={gid(g):x.copy() for g,x in nba[nba.GAME_ID.map(gid).isin(games)].groupby("GAME_ID",sort=False)}
    pg={gid(g):x.copy() for g,x in pbp[pbp.GAMEID.map(gid).isin(games)].groupby("GAMEID",sort=False)}
    comp={}; diag=[]; evidence=[]
    for g in sorted(games):
        if g not in ng or g not in pg:
            diag.append({"season":season,"game_id":g,"status":"SOURCE_SET_GAP","error":json.dumps({"nba":g in ng,"pbp":g in pg})}); continue
        try:
            rows,ev=build_team_rows(g,ng[g],pg[g]); comp.update({(g,tid(r["team_id"])):r for r in rows}); evidence.extend(ev); diag.append({"season":season,"game_id":g,"status":"PASS_INVARIANT_TEAM_TOTAL","error":""})
        except Exception as e:
            diag.append({"season":season,"game_id":g,"status":"FAIL_CLOSED","error":f"{type(e).__name__}: {e}"})
    cr=[]
    for k,expected in sorted(controls.items()):
        r=comp.get(k)
        if r is None:
            cr.append({"season":season,"game_id":k[0],"team_id":k[1],"computed":False,"match":False,"failure":"NO_COMPUTED_CONTROL"}); continue
        got=tuple(int(r[z]) for z in REQT); cr.append({"season":season,"game_id":k[0],"team_id":k[1],"computed":True,"match":got==expected,"failure":"",**{f"expected_{z}":expected[i] for i,z in enumerate(REQT)},**{f"computed_{z}":got[i] for i,z in enumerate(REQT)}})
    tr=[]
    for g,teams in sorted(targets.items()):
        for t in sorted(teams):
            r=comp.get((g,t))
            if r is not None: tr.append({"season":season,"game_id":g,"team_id":t,**{z:int(r[z]) for z in REQT},"provenance":"exact team total: PBP Stats rebound universe + NBA V2 invariant real-rebound status across all narrow-clock rebound candidates; no lineup/event-identity choice"})
    pd.DataFrame(cr).to_csv(args.output_dir/"TEAM_INVARIANT_CONTROLS.csv",index=False); pd.DataFrame(tr).to_csv(args.output_dir/"TEAM_INVARIANT_TARGET_CANDIDATES.csv",index=False); pd.DataFrame(diag).to_csv(args.output_dir/"TEAM_INVARIANT_GAME_DIAGNOSTICS.csv",index=False); pd.DataFrame(evidence).to_csv(args.output_dir/"TEAM_INVARIANT_EVIDENCE.csv",index=False)
    computed=sum(bool(r.get("computed")) for r in cr); mismatches=sum(bool(r.get("computed")) and not bool(r.get("match")) for r in cr)
    qa={"status":"PASS","season":season,"control_rows":len(cr),"computed_controls":computed,"control_mismatches":mismatches,"target_team_rows":sum(len(x) for x in targets.values()),"target_candidates":len(tr),"integrity":{"pbpstats_rebound_universe":True,"nba_v2_real_rebound_status_required":True,"unmatched_rows_require_invariant_real_status_across_all_narrow_clock_rebound_candidates":True,"lineup_reconstruction_used":False,"event_identity_inference_used":False,"opponent_rebound_inference_used":False,"empirical_model_used":False,"rounded_percentage_backsolve_used":False}}
    (args.output_dir/"TEAM_INVARIANT_QA.json").write_text(json.dumps(qa,indent=2)+"\n"); print(json.dumps(qa,indent=2),flush=True)
if __name__=="__main__":main()
