#!/usr/bin/env python3
"""2025-26 cross-feed rebound semantic audit using stable event identity.

Diagnostic only.  Cumulative player rebound counters are treated as mutable
scorekeeper metadata, not event identity.  The candidate identity is:
  player rebound: game, period, clock, personId, offensive/defensive kind
  team rebound:   game, period, clock, teamId, offensive/defensive kind
with Counter multiplicity preserved.  The audit also reports duplicate-signature
collisions, counter disagreements on uniquely paired rows, and every one-sided row.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

import pandas as pd

TEAM_ID_MIN = 1_610_612_737
COUNTER_RE = re.compile(r"\(\s*off\s*:\s*(\d+)\s+def\s*:\s*(\d+)\s*\)", re.I)


def norm(v: object) -> str:
    return "" if pd.isna(v) else re.sub(r"\s+", " ", str(v)).strip().lower()


def norm_kind(row: pd.Series) -> str | None:
    s = norm(row.get("subType"))
    if s in {"offensive", "off", "oreb"}:
        return "OREB"
    if s in {"defensive", "def", "dreb"}:
        return "DREB"
    d = norm(row.get("description"))
    if "offensive rebound" in d:
        return "OREB"
    if "defensive rebound" in d:
        return "DREB"
    return None


def player_id(row: pd.Series) -> int | None:
    x = pd.to_numeric(pd.Series([row.get("personId")]), errors="coerce").iloc[0]
    if pd.isna(x):
        return None
    x = int(x)
    return x if 0 < x < TEAM_ID_MIN else None


def team_id(row: pd.Series) -> int | None:
    x = pd.to_numeric(pd.Series([row.get("teamId")]), errors="coerce").iloc[0]
    if pd.notna(x) and int(x) >= TEAM_ID_MIN:
        return int(x)
    x = pd.to_numeric(pd.Series([row.get("personId")]), errors="coerce").iloc[0]
    if pd.notna(x) and int(x) >= TEAM_ID_MIN:
        return int(x)
    return None


def sig(row: pd.Series):
    gid = int(row.gameId); per = int(row.period); clock = str(row.clock); kind = norm_kind(row)
    pid = player_id(row)
    if pid is not None:
        return ("player", gid, per, clock, pid, kind)
    return ("team", gid, per, clock, team_id(row), kind)


def counters(row: pd.Series):
    vals=[]
    for k in ("reboundTotal", "reboundOffensiveTotal", "reboundDefensiveTotal"):
        x=pd.to_numeric(pd.Series([row.get(k)]),errors="coerce").iloc[0]
        vals.append(None if pd.isna(x) else int(x))
    if vals[1] is None or vals[2] is None:
        m=COUNTER_RE.search(str(row.get("description", "")))
        if m:
            if vals[1] is None: vals[1]=int(m.group(1))
            if vals[2] is None: vals[2]=int(m.group(2))
    if vals[0] is None and vals[1] is not None and vals[2] is not None:
        vals[0]=vals[1]+vals[2]
    return tuple(vals)


def rec(row: pd.Series) -> dict:
    out={}
    for k in ["gameId","actionNumber","orderNumber","clock","period","actionType","subType","description","personId","playerName","teamId","teamTricode","reboundTotal","reboundDefensiveTotal","reboundOffensiveTotal","possession"]:
        if k in row.index:
            v=row[k]
            out[k]=None if pd.isna(v) else (v.item() if hasattr(v,"item") else v)
    out["stable_signature"]=list(sig(row))
    out["parsed_counters"]=list(counters(row))
    return out


def rows_for_residual(frame: pd.DataFrame, residual: Counter) -> list[dict]:
    need=residual.copy(); out=[]
    for _,r in frame.sort_values(["gameId","period","actionNumber"],kind="stable").iterrows():
        s=sig(r)
        if need[s]>0:
            out.append(rec(r)); need[s]-=1
    return out


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--cdn",type=Path,required=True)
    ap.add_argument("--nba",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args()
    cdn=pd.read_csv(a.cdn,low_memory=False); nba=pd.read_csv(a.nba,low_memory=False)
    for f in (cdn,nba):
        f["action_norm"]=f.actionType.astype("string").fillna("").str.strip().str.lower()
        f["gameId"]=pd.to_numeric(f.gameId,errors="raise").astype("int64")
        f["period"]=pd.to_numeric(f.period,errors="raise").astype("int64")
        f["actionNumber"]=pd.to_numeric(f.actionNumber,errors="raise").astype("int64")
    c=cdn[cdn.action_norm.eq("rebound")].copy(); n=nba[nba.action_norm.eq("rebound")].copy()
    assert len(c)>0 and len(n)>0

    cs=Counter(sig(r) for _,r in c.iterrows()); ns=Counter(sig(r) for _,r in n.iterrows())
    shared=cs & ns; cres=cs-ns; nres=ns-cs
    collisions_c={s:k for s,k in cs.items() if k>1}; collisions_n={s:k for s,k in ns.items() if k>1}
    collision_sigs=set(collisions_c)|set(collisions_n)

    # On signatures unique in both feeds, quantify how often mutable counters differ.
    cgroups=defaultdict(list); ngroups=defaultdict(list)
    for _,r in c.iterrows(): cgroups[sig(r)].append(r)
    for _,r in n.iterrows(): ngroups[sig(r)].append(r)
    unique_pairs=0; counter_equal=0; counter_different=[]; action_equal=0; action_different=0
    for s in sorted(set(cgroups)&set(ngroups), key=str):
        if len(cgroups[s])!=1 or len(ngroups[s])!=1: continue
        cr=cgroups[s][0]; nr=ngroups[s][0]; unique_pairs+=1
        cc=counters(cr); nc=counters(nr)
        if cc==nc: counter_equal+=1
        else:
            counter_different.append({"signature":list(s),"cdn_actionNumber":int(cr.actionNumber),"nba_actionNumber":int(nr.actionNumber),"cdn_counters":list(cc),"nba_counters":list(nc),"cdn_description":str(cr.description),"nba_description":str(nr.description)})
        if int(cr.actionNumber)==int(nr.actionNumber): action_equal+=1
        else: action_different+=1

    missing_kind_c=[rec(r) for _,r in c.iterrows() if norm_kind(r) is None]
    missing_kind_n=[rec(r) for _,r in n.iterrows() if norm_kind(r) is None]
    missing_entity_c=[rec(r) for _,r in c.iterrows() if player_id(r) is None and team_id(r) is None]
    missing_entity_n=[rec(r) for _,r in n.iterrows() if player_id(r) is None and team_id(r) is None]

    out={
      "status":"DIAGNOSTIC_ONLY",
      "candidate_identity":"(entity_type, gameId, period, clock, playerId-or-teamId, OREB/DREB), preserving multiplicity",
      "counts":{
        "cdn_rebounds":len(c),"nba_rebounds":len(n),"shared":sum(shared.values()),
        "cdn_residual":sum(cres.values()),"nba_residual":sum(nres.values()),
        "cdn_collision_signatures":len(collisions_c),"nba_collision_signatures":len(collisions_n),"union_collision_signatures":len(collision_sigs),
        "unique_cross_feed_pairs":unique_pairs,"counter_equal_unique_pairs":counter_equal,"counter_different_unique_pairs":len(counter_different),
        "action_number_equal_unique_pairs":action_equal,"action_number_different_unique_pairs":action_different,
        "cdn_missing_kind":len(missing_kind_c),"nba_missing_kind":len(missing_kind_n),
        "cdn_missing_entity":len(missing_entity_c),"nba_missing_entity":len(missing_entity_n),
      },
      "cdn_residual_rows":rows_for_residual(c,cres),
      "nba_residual_rows":rows_for_residual(n,nres),
      "collision_signatures":[{"signature":list(s),"cdn_multiplicity":cs.get(s,0),"nba_multiplicity":ns.get(s,0)} for s in sorted(collision_sigs,key=str)],
      "counter_differences_unique_pairs":counter_different,
      "cdn_missing_kind_rows":missing_kind_c,"nba_missing_kind_rows":missing_kind_n,
      "cdn_missing_entity_rows":missing_entity_c,"nba_missing_entity_rows":missing_entity_n,
    }
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2,default=str)+"\n")
    print(json.dumps(out["counts"],indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
