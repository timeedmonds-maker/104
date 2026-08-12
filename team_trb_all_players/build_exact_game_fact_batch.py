#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
import build_exact_game_fact_layer as base
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--season', required=True)
    ap.add_argument('--nba', type=Path, required=True)
    ap.add_argument('--v3', type=Path, required=True)
    ap.add_argument('--pbp', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--batch-index', type=int, required=True)
    ap.add_argument('--batch-size', type=int, default=100)
    args=ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    nba=io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3=lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp=io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
    nba_groups={int(gid):f.copy() for gid,f in nba.groupby('GAME_ID', sort=False)}
    v3_groups={int(gid):f.copy() for gid,f in v3.groupby('gameId', sort=False)}
    pbp_groups={int(gid):f.copy() for gid,f in pbp.groupby('GAMEID', sort=False)}
    all_ids=sorted(set(nba_groups)&set(v3_groups)&set(pbp_groups))
    start=args.batch_index*args.batch_size
    stop=min(len(all_ids), start+args.batch_size)
    ids=all_ids[start:stop]
    team_rows=[]; player_rows=[]; audits=[]; failures=[]
    for i,gid in enumerate(ids,1):
        try:
            tr,pr,audit=base.build_game(gid,nba_groups[gid],v3_groups[gid],pbp_groups[gid])
            team_rows.extend(tr); player_rows.extend(pr); audits.append(audit)
        except Exception as exc:
            failures.append({'game_id':int(gid),'error':f'{type(exc).__name__}: {exc}'})
        if i%20==0 or i==len(ids):
            print(f'BATCH_PROGRESS season={args.season} batch={args.batch_index} processed={i}/{len(ids)} success={len(audits)} failures={len(failures)}', flush=True)
    pd.DataFrame(team_rows).to_csv(args.output_dir/'team_game_treb.csv.gz',index=False,compression='gzip')
    pd.DataFrame(player_rows).to_csv(args.output_dir/'player_game_treb_on.csv.gz',index=False,compression='gzip')
    (args.output_dir/'game_audit.json').write_text(json.dumps(audits,indent=2)+'\n')
    (args.output_dir/'failures.json').write_text(json.dumps(failures,indent=2)+'\n')
    qa={'season':args.season,'batch_index':args.batch_index,'batch_size':args.batch_size,'total_common_games':len(all_ids),'start_index':start,'stop_index':stop,'games_requested':len(ids),'successful_games':len(audits),'failed_games':len(failures),'status':'PASS' if not failures else 'REPAIR_REQUIRED'}
    (args.output_dir/'qa.json').write_text(json.dumps(qa,indent=2)+'\n')
    print(json.dumps(qa,indent=2), flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
