#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests

import adams_rebound_classifier_audit_chunk as audit
import run_local_treb_production as io
import production_treb_engine as rebound_engine
import production_treb_engine_v3 as lineup_engine
import local_treb_rebuild as core


def fetch_endpoint(gid: int, typ: str, attempts: int = 18) -> tuple[dict | None, list[dict]]:
    history=[]
    for attempt in range(1, attempts + 1):
        started=time.time()
        try:
            r=requests.get(audit.API, params={"GameId":f"{gid:010d}","Type":typ}, timeout=30)
            history.append({"attempt":attempt,"status_code":r.status_code,"elapsed_s":round(time.time()-started,3)})
            if r.ok:
                return r.json(), history
        except Exception as exc:
            history.append({"attempt":attempt,"error":f"{type(exc).__name__}: {exc}","elapsed_s":round(time.time()-started,3)})
        # Deliberately modest backoff: this is one diagnostic game, not a bulk API scrape.
        time.sleep(min(10.0, 1.0 + attempt * 0.75))
    return None, history


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--nba',type=Path,required=True)
    ap.add_argument('--v3',type=Path,required=True)
    ap.add_argument('--pbp',type=Path,required=True)
    ap.add_argument('--game-id',type=int,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    gid=int(args.game_id)

    previous=core.STARTER_REPAIRS.get(audit.REPAIR_KEY)
    core.STARTER_REPAIRS[audit.REPAIR_KEY]=audit.REPAIR
    payload={"game_id":gid,"status":"PARTIAL","api_attempts":{}}
    try:
        nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False))
        v3=lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False))
        pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
        ng=nba[nba.GAME_ID.eq(gid)].copy(); vg=v3[v3.gameId.eq(gid)].copy(); pg=pbp[pbp.GAMEID.eq(gid)].copy()
        if ng.empty or vg.empty or pg.empty:
            raise RuntimeError('missing local source game')
        lu=lineup_engine.reconstruct_game_lineups(ng,vg)
        joined,join_audit=rebound_engine.join_pbp_rebounds(lu,pg)
        if int(join_audit.get('unmatched_rebound_bearing_rows',0)):
            raise RuntimeError(f"unmatched={join_audit['unmatched_rebound_bearing_rows']}")
        joined=rebound_engine.classify_rebounds(joined)
        local={}
        for mode in audit.MODES:
            vals=audit.aggregate(joined,mode); vals['seconds_on']=int(lu.seconds.get(audit.ADAMS,0)); local[mode]=vals
        payload['local']=local; payload['join_audit']=join_audit
        payload['local_discriminates_current_vs_real_first']=local['current'] != local['real_first']
        payload['current_minus_real_first']={k:int(local['current'].get(k,0)-local['real_first'].get(k,0)) for k in audit.LOCKED}

        # Fetch the historically less reliable opponent endpoint first so a later
        # Lineup success cannot be wasted if the opponent endpoint is transiently bad.
        opp,opp_hist=fetch_endpoint(gid,'LineupOpponent')
        payload['api_attempts']['LineupOpponent']=opp_hist
        line,line_hist=fetch_endpoint(gid,'Lineup')
        payload['api_attempts']['Lineup']=line_hist
        if opp is None or line is None:
            payload['status']='API_INCOMPLETE'
        else:
            side=audit.side(line); lm,om=audit.rowmap(line,side),audit.rowmap(opp,side)
            if set(lm)!=set(om):
                raise RuntimeError('Lineup/LineupOpponent entity mismatch')
            truth=audit.blank_totals()
            for eid,r in lm.items():
                if str(audit.ADAMS) not in eid.split('-'):
                    continue
                o=om[eid]
                truth['seconds_on']+=audit.secs(r.get('Minutes'))
                truth['team_oreb_on']+=audit.n(r,'OffRebounds')
                truth['team_dreb_on']+=audit.n(r,'DefRebounds')
                truth['opponent_oreb_on']+=audit.n(o,'OffRebounds')
                truth['opponent_dreb_on']+=audit.n(o,'DefRebounds')
            payload['truth']=truth
            payload['diffs']={m:audit.diffs(vals,truth) for m,vals in local.items()}
            payload['exact']={m:all(x==0 for x in payload['diffs'][m].values()) for m in audit.MODES}
            payload['status']='COMPLETE'
    except Exception as exc:
        payload['error']=f'{type(exc).__name__}: {exc}'
        if payload.get('status')=='PARTIAL': payload['status']='ERROR'
    finally:
        if previous is None: core.STARTER_REPAIRS.pop(audit.REPAIR_KEY,None)
        else: core.STARTER_REPAIRS[audit.REPAIR_KEY]=previous

    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2)+'\n')
    print(json.dumps({k:v for k,v in payload.items() if k not in {'api_attempts'}},indent=2),flush=True)
    return 0 if payload['status']=='COMPLETE' else 2

if __name__=='__main__':
    raise SystemExit(main())
