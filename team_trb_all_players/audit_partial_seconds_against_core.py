#!/usr/bin/env python3
"""Fail-closed audit: reconstructed partial V2 ON seconds must equal locked core PTS seconds.

The historical core ON-court seconds are retained source-of-truth.  Correcting
OFF windows for trades/tenure must never lose or invent ON-court playing time.
This catches bad transaction boundaries, rescinded trades, missing return
stints, and lineup reconstruction errors before final assembly.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

BASE=Path(__file__).resolve().parent
IMPACT=BASE/"impact_database"
CORE_OUT=IMPACT/"outputs"


def load_core_seconds() -> pd.DataFrame:
    parts=[]
    for p in sorted(CORE_OUT.glob("*/player_team_totals.csv.gz")):
        d=pd.read_csv(p,compression="gzip",usecols=["season","team_id","EntityId","Name","SecondsPlayed"])
        d=d.rename(columns={"EntityId":"player_id","Name":"core_player","SecondsPlayed":"core_seconds_on"})
        d["player_id"]=d.player_id.astype(str); d["team_id"]=d.team_id.astype(int)
        parts.append(d)
    core=pd.concat(parts,ignore_index=True)
    if len(core)!=14526: raise RuntimeError(f"expected 14526 raw core PTS, got {len(core)}")
    return core


def load_segments(path: Path) -> pd.DataFrame:
    if path.suffix==".csv":
        d=pd.read_csv(path,dtype={"player_id":str})
    else:
        rows=[]
        opener=gzip.open if path.suffix==".gz" else open
        with opener(path,"rt",encoding="utf-8") as f:
            for line in f:
                if line.strip(): rows.append(json.loads(line))
        d=pd.DataFrame(rows)
    d["player_id"]=d.player_id.astype(str); d["team_id"]=d.team_id.astype(int)
    return d


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("segments",type=Path)
    ap.add_argument("--out",type=Path,default=IMPACT/"final_integrity_rebuild"/"partial_seconds_reconciliation.csv")
    ap.add_argument("--summary",type=Path,default=IMPACT/"final_integrity_rebuild"/"partial_seconds_reconciliation_summary.json")
    ap.add_argument("--allow-mismatch",action="store_true",help="diagnostic only; certification must not use this")
    args=ap.parse_args()
    seg=load_segments(args.segments)
    if len(seg)!=5199: raise RuntimeError(f"expected 5199 segment rows, got {len(seg)}")
    pts=(seg.groupby(["season","team_id","player_id"],as_index=False)
            .agg(player=("player","first"),seconds_on=("seconds_on","sum"),segment_rows=("player_id","size")))
    if len(pts)!=4877: raise RuntimeError(f"expected 4877 partial PTS, got {len(pts)}")
    core=load_core_seconds()
    merged=pts.merge(core,on=["season","team_id","player_id"],how="left",validate="one_to_one")
    if merged.core_seconds_on.isna().any(): raise RuntimeError("partial PTS missing locked-core seconds")
    merged["seconds_diff"]=merged.seconds_on-merged.core_seconds_on
    merged["abs_seconds_diff"]=merged.seconds_diff.abs()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    merged.sort_values(["abs_seconds_diff","season"],ascending=[False,True]).to_csv(args.out,index=False)
    summary={
        "partial_pts":len(merged),
        "exact_seconds_matches":int(merged.seconds_diff.eq(0).sum()),
        "seconds_mismatches":int(merged.seconds_diff.ne(0).sum()),
        "abs_diff_gt_1":int(merged.abs_seconds_diff.gt(1).sum()),
        "abs_diff_gt_60":int(merged.abs_seconds_diff.gt(60).sum()),
        "abs_diff_gt_2880":int(merged.abs_seconds_diff.gt(2880).sum()),
        "min_seconds_diff":float(merged.seconds_diff.min()),
        "max_seconds_diff":float(merged.seconds_diff.max()),
        "status":"PASS" if merged.seconds_diff.eq(0).all() else "FAIL_REPAIR_REQUIRED",
        "policy":"All 4,877 reconstructed partial PTS must exactly reconcile ON seconds to the locked historical core before final merge."
    }
    args.summary.write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))
    if summary["status"]!="PASS" and not args.allow_mismatch: return 2
    return 0

if __name__=="__main__":
    raise SystemExit(main())
