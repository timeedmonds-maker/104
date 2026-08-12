#!/usr/bin/env python3
"""Reaggregate Adams classifier artifacts using successful per-game records only.

Older microchunk artifacts may contain chunk-level method totals for games whose
local reconstruction succeeded but whose independent PBP Stats API truth later
failed. Those chunk totals must not be compared with the smaller API-truth set.
This postprocessor ignores all precomputed method totals and rebuilds every
comparison solely from `per_game`, where both local output and API truth exist.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

KEYS=("seconds_on","team_oreb_on","team_dreb_on","opponent_oreb_on","opponent_dreb_on")
MODES=("current","real_first","description_counter")


def zero(): return {k:0 for k in KEYS}

def add(dst,src):
    for k in KEYS: dst[k]+=int(src.get(k,0))

def diff(a,b): return {k:int(a[k]-b[k]) for k in KEYS}


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--input-dir',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()

    payloads=[]
    for p in sorted(args.input_dir.rglob('*.json')):
        try:
            d=json.loads(p.read_text())
        except Exception:
            continue
        if isinstance(d,dict) and isinstance(d.get('per_game'),list):
            payloads.append((p,d))

    # A game may appear in a pair artifact and a targeted retry. Prefer the last
    # successfully parsed record in deterministic path order; identical records
    # remain identical, while a later targeted truth retry can replace a partial.
    by_game={}
    failures=[]
    for p,d in payloads:
        for f in d.get('failures',[]): failures.append({'source':str(p),'chunk_index':d.get('chunk_index'),'failure':f})
        for g in d.get('per_game',[]): by_game[int(g['game_id'])]={'source':str(p),'record':g}

    truth=zero()
    methods={m:{'observed':zero(),'exact_games':0,'sum_abs_game_error':zero(),'nonexact_games':[]} for m in MODES}
    discriminators=[]
    for gid in sorted(by_game):
        g=by_game[gid]['record']; add(truth,g['truth'])
        current=g['local']['current']; real=g['local']['real_first']
        if any(int(current.get(k,0))!=int(real.get(k,0)) for k in KEYS):
            discriminators.append({'game_id':gid,'current':{k:int(current.get(k,0)) for k in KEYS},'real_first':{k:int(real.get(k,0)) for k in KEYS},'truth':{k:int(g['truth'][k]) for k in KEYS}})
        for m in MODES:
            local=g['local'][m]; add(methods[m]['observed'],local)
            ds=diff({k:int(local.get(k,0)) for k in KEYS},g['truth'])
            exact=all(v==0 for v in ds.values())
            methods[m]['exact_games']+=int(exact)
            for k,v in ds.items(): methods[m]['sum_abs_game_error'][k]+=abs(v)
            if not exact: methods[m]['nonexact_games'].append({'game_id':gid,'diff':ds})
    for m in MODES: methods[m]['diff_vs_truth']=diff(methods[m]['observed'],truth)

    out={
        'status':'PER_GAME_ONLY_REAGGREGATION',
        'artifact_payloads_read':len(payloads),
        'games_with_independent_truth':len(by_game),
        'game_ids':sorted(by_game),
        'api_truth':truth,
        'methods':methods,
        'current_vs_real_first_discriminating_games_with_truth':discriminators,
        'artifact_failures_seen':failures,
        'note':'All comparison totals are rebuilt only from per_game records containing independent API truth; contaminated chunk-level observed totals are ignored.'
    }
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k not in {'artifact_failures_seen'}},indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
