#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import run_local_treb_production as io
import production_treb_engine_v3 as v3engine
import local_treb_rebuild as core

PATTERN = re.compile(
    r"game=(?P<game>\d+) period=(?P<period>\d+) team=(?P<team>\d+).*?"
    r"event_num': (?P<event>\d+), 'player_id': (?P<player>\d+), 'event_type': (?P<etype>\d+)",
    re.S,
)


def clean(value):
    if isinstance(value, (pd.Timestamp,)):
        return str(value)
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try: return value.item()
        except Exception: pass
    return value


def row_dict(row: pd.Series, columns: list[str]) -> dict:
    return {c: clean(row.get(c)) for c in columns if c in row.index}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--nba', type=Path, required=True)
    ap.add_argument('--v3', type=Path, required=True)
    ap.add_argument('--residual-json', type=Path, required=True)
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()

    residual = json.loads(args.residual_json.read_text())
    targets=[]
    for r in residual:
        err=str(r.get('error',''))
        if 'starter solution requires missing in-period lineup transition' not in err:
            continue
        m=PATTERN.search(err)
        if not m:
            targets.append({'game_id':int(r['game_id']),'parse_error':err})
            continue
        d={k:int(v) for k,v in m.groupdict().items()}
        d['game_id']=d.pop('game'); d['event_num']=d.pop('event'); d['player_id']=d.pop('player'); d['event_type']=d.pop('etype'); d['team_id']=d.pop('team')
        targets.append(d)

    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False))
    v3=v3engine.normalize_v3(pd.read_csv(args.v3,low_memory=False))
    nba['ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(nba.PERIOD,nba.PCTIMESTRING)]
    cols=['GAME_ID','PERIOD','EVENTNUM','EVENTMSGTYPE','EVENTMSGACTIONTYPE','PCTIMESTRING','ELAPSED','HOMEDESCRIPTION','NEUTRALDESCRIPTION','VISITORDESCRIPTION','PLAYER1_ID','PLAYER1_NAME','PLAYER1_TEAM_ID','PERSON1TYPE','PLAYER2_ID','PLAYER2_NAME','PLAYER2_TEAM_ID','PERSON2TYPE','PLAYER3_ID','PLAYER3_NAME','PLAYER3_TEAM_ID','PERSON3TYPE']

    out=[]
    for t in targets:
        if 'parse_error' in t:
            out.append(t); continue
        gid=t['game_id']; ev=t['event_num']; period=t['period']
        hit=nba[(nba.GAME_ID.eq(gid)) & (nba.EVENTNUM.eq(ev))]
        rec=dict(t)
        if len(hit)!=1:
            rec['source_status']=f'nba_event_hits={len(hit)}'; out.append(rec); continue
        target=hit.iloc[0]; elapsed=int(target.ELAPSED)
        same=nba[(nba.GAME_ID.eq(gid)) & (nba.PERIOD.eq(period)) & (nba.ELAPSED.between(elapsed-2,elapsed+2))].sort_values(['ELAPSED','EVENTNUM'])
        rec['source_status']='FOUND'
        rec['target_nba']=row_dict(target,cols)
        rec['nearby_nba']=[row_dict(r,cols) for _,r in same.iterrows()]
        vg=v3[(v3.gameId.eq(gid)) & (v3.period.eq(period))]
        if 'actionNumber' in vg.columns:
            exact=vg[vg.actionNumber.eq(ev)]
        else:
            exact=vg.iloc[0:0]
        rec['v3_exact_action']=[{str(k):clean(v) for k,v in r.items()} for _,r in exact.iterrows()]
        # Also capture v3 actions at the same clock when clock is available.
        clock=str(target.PCTIMESTRING)
        clock_cols=[c for c in ('clock','clockTime','timeActual') if c in vg.columns]
        same_v3=vg.iloc[0:0]
        for c in clock_cols:
            mask=vg[c].astype(str).str.contains(re.escape(clock),regex=True,na=False)
            if mask.any(): same_v3=vg[mask]; break
        rec['v3_same_clock']=[{str(k):clean(v) for k,v in r.items()} for _,r in same_v3.iterrows()]
        out.append(rec)

    payload={'year':args.year,'season':f"{args.year}-{(args.year+1)%100:02d}",'target_count':len(targets),'records':out,'status':'COMPLETE' if all(r.get('source_status')=='FOUND' for r in out) else 'PARTIAL'}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({'year':args.year,'target_count':len(targets),'status':payload['status']},indent=2),flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
