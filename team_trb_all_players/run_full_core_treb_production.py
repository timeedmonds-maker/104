#!/usr/bin/env python3
"""Run the validated TREB engines against the 9,647 full-core target manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_local_treb_production as historical

BASE=Path(__file__).resolve().parent
IMPACT=BASE/"impact_database"
TARGETS=IMPACT/"roster_tenure_v2"/"full_core_segments.jsonl.gz"
DEFAULT_RAW=IMPACT/"local_raw"
DEFAULT_OUT=IMPACT/"final_full_core_rebuild"
EXPECTED_TOTAL=9647


def seed_manifest(output: Path, batch_size: int) -> dict:
    output.mkdir(parents=True,exist_ok=True)
    p=output/"master_manifest.json"
    if p.exists():
        d=json.loads(p.read_text())
    else:
        d={"target_segments":EXPECTED_TOTAL,"batch_size":batch_size,"layer":"full_core_exact_rebuild","seasons":{}}
        historical.atomic_json(p,d)
    if int(d.get("target_segments",-1)) != EXPECTED_TOTAL:
        raise RuntimeError(f"wrong target_segments in {p}: {d.get('target_segments')}")
    return d


def audit_lock(output: Path, season: str) -> dict:
    p=output/"locked_seasons"/f"{season}.json"
    d=json.loads(p.read_text())
    diffs=[]
    for r in d.get("targets",[]):
        expected=int(r["expected_seconds_on"]); actual=int(r["seconds_on"])
        if expected != actual:
            diffs.append({"player_id":str(r["player_id"]),"team_id":int(r["team_id"]),"expected_seconds_on":expected,"actual_seconds_on":actual,"diff_seconds":actual-expected})
    audit={"season":season,"target_count":int(d.get("target_count",0)),"status":d.get("status"),"seconds_exact_matches":int(d.get("target_count",0))-len(diffs),"seconds_mismatches":len(diffs),"seconds_mismatch_rows":diffs}
    historical.atomic_json(output/"seconds_audit"/f"{season}.json",audit)
    return audit


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--years",nargs="+",type=int,default=list(range(2000,2025)))
    ap.add_argument("--raw",type=Path,default=DEFAULT_RAW)
    ap.add_argument("--output",type=Path,default=DEFAULT_OUT)
    ap.add_argument("--batch-size",type=int,default=50)
    ap.add_argument("--force",action="store_true")
    args=ap.parse_args()
    if not TARGETS.exists(): raise FileNotFoundError(TARGETS)
    manifest=seed_manifest(args.output,args.batch_size)
    historical.TARGETS_PATH=TARGETS
    for year in args.years:
        historical.process_season(year,args,manifest,historical.git_commit())
        season=historical.season_name(year)
        a=audit_lock(args.output,season)
        print("FULL_CORE_SECONDS_AUDIT",json.dumps({k:v for k,v in a.items() if k!="seconds_mismatch_rows"},sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
