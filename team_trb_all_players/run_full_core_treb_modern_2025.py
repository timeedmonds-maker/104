#!/usr/bin/env python3
"""Run the validated 2025-26 modern TREB engine against full-core targets."""
from __future__ import annotations
import json
from pathlib import Path
import run_local_treb_production as base
import run_modern_treb_production_2025 as modern

BASE=Path(__file__).resolve().parent
IMPACT=BASE/"impact_database"
TARGETS=IMPACT/"roster_tenure_v2"/"full_core_segments.jsonl.gz"
OUT=IMPACT/"final_full_core_rebuild"
EXPECTED_TOTAL=9647


def main() -> int:
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=OUT/"master_manifest.json"
    if not manifest.exists():
        base.atomic_json(manifest,{"target_segments":EXPECTED_TOTAL,"layer":"full_core_exact_rebuild","seasons":{}})
    d=json.loads(manifest.read_text())
    if int(d.get("target_segments",-1))!=EXPECTED_TOTAL: raise RuntimeError(d)
    base.TARGETS_PATH=TARGETS
    modern.DEFAULT_OUT=OUT
    rc=modern.main()
    lock=OUT/"locked_seasons"/"2025-26.json"
    state=json.loads(lock.read_text())
    diffs=[]
    for r in state["targets"]:
        expected=int(r["expected_seconds_on"]); actual=int(r["seconds_on"])
        if expected!=actual:
            diffs.append({"player_id":str(r["player_id"]),"team_id":int(r["team_id"]),"expected_seconds_on":expected,"actual_seconds_on":actual,"diff_seconds":actual-expected})
    base.atomic_json(OUT/"seconds_audit"/"2025-26.json",{"season":"2025-26","target_count":len(state["targets"]),"seconds_exact_matches":len(state["targets"])-len(diffs),"seconds_mismatches":len(diffs),"seconds_mismatch_rows":diffs})
    return rc

if __name__=="__main__":
    raise SystemExit(main())
