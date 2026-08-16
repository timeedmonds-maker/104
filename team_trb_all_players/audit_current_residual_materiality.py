#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import pathlib
import collections
from typing import Dict, Tuple, List

import pandas as pd

import audit_ambiguous_starter_materiality as mat

REQP = ("seconds_on","team_oreb_on","team_dreb_on","opponent_oreb_on","opponent_dreb_on")
REQT = ("team_oreb","team_dreb","opponent_oreb","opponent_dreb")
STATE_FIELDS = REQP + REQT

def pid(x):
    s=str(x).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s

def tid(x):
    return str(int(float(x)))

def gid(x):
    s=str(x).strip()
    if s.endswith(".0"): s=s[:-2]
    try: return str(int(float(s))).zfill(10)
    except: return s.zfill(10)

def key(r):
    return (str(r["season"]), tid(r["team_id"]), pid(r["player_id"]))

def find_one(root, pattern):
    xs=list(pathlib.Path(root).rglob(pattern))
    if len(xs)!=1:
        raise RuntimeError(f"expected one {pattern} under {root}, found {len(xs)}")
    return xs[0]

def load_current(root):
    return list(csv.DictReader(open(find_one(root,"AUTONOMOUS_BLOCKER_MANIFEST.csv"), newline="")))

def inject_player_file(facts, path, label):
    n=0
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            k=(gid(r["game_id"]),tid(r["team_id"]),pid(r["player_id"]))
            vals={z:float(r[z]) for z in REQP}
            old=facts.get(k,{})
            for z,v in vals.items():
                if z in old and abs(old[z]-v)>1e-9:
                    raise RuntimeError(f"CONFLICT {label} {k} {z} {old[z]} {v}")
                facts[k][z]=v
            n+=1
    return n

def load_facts(args):
    facts=collections.defaultdict(dict)
    cp=find_one(args.consensus_dir,"PROMOTABLE_RETAINED_FACT_CONSENSUS.csv")
    with open(cp,newline="") as f:
        for r in csv.DictReader(f):
            facts[(gid(r["game_id"]),tid(r["team_id"]),pid(r.get("player_id","")))][r["field"]]=float(r["value"])
    injected={}
    for root,pat,label in [
        (args.recovered_old_dir,"RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz","recovered_old"),
        (args.recovered_mid_dir,"RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz","recovered_mid"),
        (args.recovered_new_dir,"RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz","recovered_new"),
    ]:
        injected[label]=inject_player_file(facts,find_one(root,pat),label)

    overrides={}
    pp=find_one(args.pbp_dir,"TREB_143_V2_PBP_EXACT_AUDIT.csv")
    with open(pp,newline="") as f:
        for r in csv.DictReader(f):
            if r.get("status")!="PASS_EXACT" or str(r.get("validation_pass")).strip().lower() not in {"true","1"}:
                continue
            k=(gid(r["game_id"]),tid(r["team_id"]))
            v={z:float(r[z]) for z in REQT}
            if k in overrides and any(abs(overrides[k][z]-v[z])>1e-9 for z in REQT):
                raise RuntimeError(f"CONFLICT_TEAM {k}")
            overrides[k]=v
    return facts,overrides,injected

def load_targets(path, season, wanted):
    out={}
    with gzip.open(path,"rt",encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            r=json.loads(line)
            if str(r.get("season"))!=season: continue
            k=key(r)
            if k in wanted: out[k]=r
    return out

def load_tenure_games(args, season, wanted, targets):
    schedule={}
    ap=find_one(args.tenure_dir,"TREB_949_RETAINED_SCHEDULE_AUDITED_TENURE_AUDIT.csv")
    with open(ap,newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("season"))!=season: continue
            if r.get("status")!="EXACT_RETAINED_SCHEDULE_AUDITED_TENURE_IDENTITY": continue
            k=key(r)
            if k not in wanted: continue
            gs={gid(x) for x in str(r.get("game_ids") or "").split("|") if str(x).strip()}
            expected=int(float(r.get("expected_team_games") or 0))
            if len(gs)==expected: schedule[k]=gs

    ledger=collections.defaultdict(set)
    ledger_seconds={}
    with gzip.open(args.ledger,"rt",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            try:k=key(r)
            except:continue
            if k not in wanted or k[0]!=season: continue
            g=gid(r["game_id"])
            ledger[k].add(g)
            ledger_seconds[(k,g)]=float(r.get("seconds_game") or 0)

    exact={}
    source={}
    for k in wanted:
        if k in schedule:
            exact[k]=schedule[k]; source[k]="schedule_audited_tenure_identity"
            continue
        t=targets.get(k)
        expected=int(float((t or {}).get("team_games_in_tenure") or 0))
        gs=set(ledger.get(k,set()))
        if gs and len(gs)==expected:
            exact[k]=gs; source[k]="exact_v3_roster_ledger_tenure_identity"
    return exact,source,ledger_seconds

def team_fact(facts,overrides,g,t):
    cv=facts.get((g,t,""),{})
    if all(z in cv for z in REQT):
        return {z:float(cv[z]) for z in REQT}
    ov=overrides.get((g,t))
    return dict(ov) if ov is not None else None

def player_fact(facts,g,t,p):
    cv=facts.get((g,t,p),{})
    if all(z in cv for z in REQP):
        return {z:float(cv[z]) for z in REQP}
    return None

def row_tuple(tv,pv):
    return tuple(float(pv[z]) for z in REQP) + tuple(float(tv[z]) for z in REQT)

def add_state(a,b):
    return tuple(a[i]+b[i] for i in range(len(STATE_FIELDS)))

def zero_state():
    return tuple(0.0 for _ in STATE_FIELDS)

def validate_option(tv,pv):
    comps=(tv["team_oreb"]-pv["team_oreb_on"],
           tv["team_dreb"]-pv["team_dreb_on"],
           tv["opponent_oreb"]-pv["opponent_oreb_on"],
           tv["opponent_dreb"]-pv["opponent_dreb_on"])
    return min(comps)>=-1e-9

def metric(st):
    d={STATE_FIELDS[i]:float(st[i]) for i in range(len(STATE_FIELDS))}
    ton=d["team_oreb_on"]+d["team_dreb_on"]
    oon=d["opponent_oreb_on"]+d["opponent_dreb_on"]
    tt=d["team_oreb"]+d["team_dreb"]
    ot=d["opponent_oreb"]+d["opponent_dreb"]
    toff=tt-ton; ooff=ot-oon
    if min(ton,oon,toff,ooff)<-1e-9 or ton+oon<=0 or toff+ooff<=0:
        raise ValueError("invalid rebound denominator/complement")
    on=100.0*ton/(ton+oon)
    off=100.0*toff/(toff+ooff)
    return on,off,on-off

def variant_options_for_key(variants, target_tid, target_pid, exact_tv, exact_pv):
    opts={}
    for v in variants:
        trs=[r for r in v["team_rows"] if tid(r["team_id"])==target_tid]
        if len(trs)!=1: continue
        tr=trs[0]
        tv={z:float(tr[z]) for z in REQT}
        if exact_tv is not None and any(abs(tv[z]-exact_tv[z])>1e-9 for z in REQT):
            continue

        same=[r for r in v["player_rows"] if pid(r["player_id"])==target_pid and tid(r["team_id"])==target_tid]
        other=[r for r in v["player_rows"] if pid(r["player_id"])==target_pid and tid(r["team_id"])!=target_tid]
        if other:
            continue
        if len(same)>1: continue
        if same:
            pr=same[0]; pv={z:float(pr[z]) for z in REQP}
        else:
            pv={z:0.0 for z in REQP}
        if exact_pv is not None and any(abs(pv[z]-exact_pv[z])>1e-9 for z in REQP):
            continue
        if not validate_option(tv,pv): continue
        tup=row_tuple(tv,pv)
        opts[tup]=tup
    return sorted(opts)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--year",type=int,required=True)
    ap.add_argument("--current-dir",type=pathlib.Path,required=True)
    ap.add_argument("--tenure-dir",type=pathlib.Path,required=True)
    ap.add_argument("--consensus-dir",type=pathlib.Path,required=True)
    ap.add_argument("--pbp-dir",type=pathlib.Path,required=True)
    ap.add_argument("--recovered-old-dir",type=pathlib.Path,required=True)
    ap.add_argument("--recovered-mid-dir",type=pathlib.Path,required=True)
    ap.add_argument("--recovered-new-dir",type=pathlib.Path,required=True)
    ap.add_argument("--targets",type=pathlib.Path,required=True)
    ap.add_argument("--ledger",type=pathlib.Path,required=True)
    ap.add_argument("--nba",type=pathlib.Path,required=True)
    ap.add_argument("--v3",type=pathlib.Path,required=True)
    ap.add_argument("--pbp",type=pathlib.Path,required=True)
    ap.add_argument("--output-dir",type=pathlib.Path,required=True)
    ap.add_argument("--threshold-pp",type=float,default=0.01)
    ap.add_argument("--minutes-gate-seconds",type=float,default=60.0)
    ap.add_argument("--max-enumeration-nodes",type=int,default=20000)
    ap.add_argument("--max-game-variants",type=int,default=5000)
    ap.add_argument("--max-key-states",type=int,default=200000)
    args=ap.parse_args()

    season=f"{args.year}-{(args.year+1)%100:02d}"
    out=args.output_dir; out.mkdir(parents=True,exist_ok=True)
    current=load_current(args.current_dir)
    season_rows=[r for r in current if str(r["season"])==season]
    wanted={key(r) for r in season_rows}
    if not wanted:
        raise RuntimeError(f"no current residual keys for {season}")
    targets=load_targets(args.targets,season,wanted)
    if len(targets)!=len(wanted):
        missing=sorted(wanted-set(targets))
        raise RuntimeError(f"missing targets {len(missing)} {missing[:5]}")
    facts,overrides,injected=load_facts(args)
    tenure_games,tenure_source,ledger_seconds=load_tenure_games(args,season,wanted,targets)

    nba=mat.io.normalize_nba(pd.read_csv(args.nba,low_memory=False))
    v3=mat.lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False))
    pbp=mat.io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={gid(g):f.copy() for g,f in nba.groupby("GAME_ID",sort=False)}
    vg={gid(g):f.copy() for g,f in v3.groupby("gameId",sort=False)}
    pg={gid(g):f.copy() for g,f in pbp.groupby("GAMEID",sort=False)}

    key_plan={}
    need_games=set()
    for k in sorted(wanted):
        if k not in tenure_games:
            key_plan[k]={"status":"AUDIT_UNRESOLVED","reason":"NO_EXACT_TENURE_IDENTITY"}
            continue
        base=zero_state(); unresolved=[]
        for g in sorted(tenure_games[k]):
            tv=team_fact(facts,overrides,g,k[1])
            pv=player_fact(facts,g,k[1],k[2])
            if pv is None:
                sec=ledger_seconds.get((k,g))
                if sec is not None and abs(sec)<=1e-9:
                    pv={z:0.0 for z in REQP}
            if tv is not None and pv is not None and validate_option(tv,pv):
                base=add_state(base,row_tuple(tv,pv))
            else:
                unresolved.append((g,tv,pv))
                need_games.add(g)
        key_plan[k]={"status":"PLAN","base":base,"unresolved":unresolved}

    variants_by_game={}
    game_meta=[]
    for g in sorted(need_games):
        if g not in ng or g not in vg or g not in pg:
            variants_by_game[g]=None
            game_meta.append({"season":season,"game_id":g,"status":"SOURCE_SET_GAP","nba":g in ng,"v3":g in vg,"pbp":g in pg})
            continue
        variants,meta=mat.enumerate_game_variants(int(g),ng[g],vg[g],pg[g],args.max_enumeration_nodes,args.max_game_variants)
        meta={"season":season,"game_id":g,"status":"ENUMERATED" if variants and not meta.get("capped") else "AUDIT_UNRESOLVED",**meta}
        variants_by_game[g]=variants if variants and not meta.get("capped") else None
        game_meta.append(meta)
        print(json.dumps({"event":"GAME_VARIANTS","season":season,"game_id":g,"variants":len(variants),"capped":meta.get("capped"),"dead":meta.get("dead_branches")}),flush=True)

    audits=[]; accepted=[]
    for k in sorted(wanted):
        plan=key_plan[k]
        t=targets[k]
        common={"season":k[0],"team_id":k[1],"player_id":k[2],"threshold_pp":args.threshold_pp,
                "minutes_gate_seconds":args.minutes_gate_seconds,"tenure_identity_source":tenure_source.get(k,"")}
        if plan["status"]!="PLAN":
            audits.append({**common,"status":"AUDIT_UNRESOLVED","reason":plan["reason"],"accepted_immaterial":False})
            continue
        states=[plan["base"]]
        unsafe=None
        option_counts=[]
        unresolved_games=[]
        for g,tv,pv in plan["unresolved"]:
            variants=variants_by_game.get(g)
            if variants is None:
                unsafe=f"NO_BOUNDED_COHERENT_VARIANTS:{g}"; unresolved_games.append(g); break
            opts=variant_options_for_key(variants,k[1],k[2],tv,pv)
            if not opts:
                unsafe=f"NO_EVIDENCE_COMPATIBLE_VARIANTS:{g}"; unresolved_games.append(g); break
            option_counts.append(len(opts))
            nxt=set()
            for st in states:
                for op in opts:
                    nxt.add(add_state(st,op))
                    if len(nxt)>args.max_key_states:
                        unsafe=f"KEY_STATE_CAP_EXCEEDED:{args.max_key_states}"; break
                if unsafe: break
            if unsafe: break
            states=sorted(nxt)
        if unsafe:
            audits.append({**common,"status":"AUDIT_UNRESOLVED","reason":unsafe,"accepted_immaterial":False,
                           "unresolved_games":"|".join(unresolved_games),"coherent_states_examined":len(states),
                           "variant_option_counts":"|".join(map(str,option_counts))})
            continue

        target_seconds=float(t.get("seconds_on") or 0)
        compatible=[]
        vals=[]
        for st in states:
            if abs(float(st[0])-target_seconds)>args.minutes_gate_seconds+1e-9:
                continue
            try:m=metric(st)
            except:continue
            compatible.append(st); vals.append(m)
        if not compatible:
            audits.append({**common,"status":"AUDIT_UNRESOLVED","reason":"NO_TARGET_MINUTE_COMPATIBLE_STATE","accepted_immaterial":False,
                           "coherent_states_examined":len(states),"target_seconds":target_seconds,
                           "seconds_min":min((s[0] for s in states),default=None),"seconds_max":max((s[0] for s in states),default=None),
                           "variant_option_counts":"|".join(map(str,option_counts))})
            continue

        onv=[x[0] for x in vals]; offv=[x[1] for x in vals]; swv=[x[2] for x in vals]
        on_range=max(onv)-min(onv); off_range=max(offv)-min(offv); sw_range=max(swv)-min(swv)
        max_range=max(on_range,off_range,sw_range)
        is_accept=max_range<=args.threshold_pp+1e-12
        status="ACCEPT_IMMATERIAL" if is_accept else "MATERIAL_REPAIR_REQUIRED"
        audits.append({**common,"status":status,"accepted_immaterial":is_accept,
                       "unresolved_game_count":len(plan["unresolved"]),
                       "unresolved_games":"|".join(g for g,_,_ in plan["unresolved"]),
                       "coherent_states_examined":len(states),"target_minute_compatible_states":len(compatible),
                       "target_seconds":target_seconds,"seconds_min":min(s[0] for s in compatible),"seconds_max":max(s[0] for s in compatible),
                       "treb_on_min":min(onv),"treb_on_max":max(onv),"treb_on_range_pp":on_range,
                       "treb_off_min":min(offv),"treb_off_max":max(offv),"treb_off_range_pp":off_range,
                       "treb_swing_min":min(swv),"treb_swing_max":max(swv),"treb_swing_range_pp":sw_range,
                       "max_range_pp":max_range,"variant_option_counts":"|".join(map(str,option_counts))})
        if is_accept:
            selected=min(compatible)
            on,off,sw=metric(selected)
            accepted.append({"season":k[0],"team_id":k[1],"player_id":k[2],"metric":"TotalReboundPct",
                             "on":on,"off_corrected":off,"on_minus_off_corrected":sw,
                             "seconds_on":selected[0],"team_games_in_tenure":len(tenure_games[k]),
                             "materiality_threshold_pp":args.threshold_pp,
                             "treb_on_range_pp":on_range,"treb_off_range_pp":off_range,"treb_swing_range_pp":sw_range,
                             "max_range_pp":max_range,
                             "provenance":"materiality-accepted: deterministic lexicographically-first coherent exact retained-source scenario; all coherent target-minute-compatible scenarios within <=0.01 pp ON/OFF/SWING"})

    pd.DataFrame(audits).to_csv(out/f"MATERIALITY_AUDIT_{args.year}.csv",index=False)
    if accepted:
        pd.DataFrame(accepted).to_csv(out/f"MATERIALITY_ACCEPTED_{args.year}.csv",index=False)
    pd.DataFrame(game_meta).to_csv(out/f"MATERIALITY_GAME_VARIANTS_{args.year}.csv",index=False)
    counts=collections.Counter(r["status"] for r in audits)
    qa={"status":"PASS","season":season,"residual_keys":len(wanted),"exact_tenure_keys":len(tenure_games),
        "games_requiring_variant_enumeration":len(need_games),"accepted_immaterial_keys":counts["ACCEPT_IMMATERIAL"],
        "material_repair_required_keys":counts["MATERIAL_REPAIR_REQUIRED"],"audit_unresolved_keys":counts["AUDIT_UNRESOLVED"],
        "threshold_pp":args.threshold_pp,"minutes_gate_seconds":args.minutes_gate_seconds,
        "exact_fact_injections":injected,
        "acceptance_rule":"all coherent exact retained-source variants satisfying the established <=60-second target-minute gate must have TREB ON, OFF, and SWING range <=0.01 percentage points",
        "representative_policy":"lexicographically-first coherent legal retained-source state; never midpoint or model estimate",
        "integrity":{"fail_closed_on_source_gap":True,"fail_closed_on_enumeration_cap":True,"fail_closed_without_exact_tenure_identity":True,
                     "empirical_model_used":False,"rounded_percentage_backsolve_used":False,
                     "opponent_rebound_inference_used":False,"partial_tenure_whole_team_subtraction_used":False}}
    (out/f"MATERIALITY_QA_{args.year}.json").write_text(json.dumps(qa,indent=2)+"\n")
    print(json.dumps(qa,indent=2),flush=True)

if __name__=="__main__":
    main()
