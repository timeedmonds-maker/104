#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import run_local_treb_production as histio
import production_treb_engine_v3 as v3engine
import modern_cdn_lineups as modern

BASE=Path(__file__).resolve().parent
TARGETS=BASE/'final_integrity_rebuild'/'EXCLUDED_GAME_REPAIR_TARGETS.json'


def normalize_v3(df: pd.DataFrame) -> pd.DataFrame:
    return v3engine.normalize_v3(df)


def normalize_cdn(df: pd.DataFrame) -> pd.DataFrame:
    out=df.copy()
    for c in ('gameId','period','actionNumber','orderNumber','personId','teamId'):
        if c in out.columns:
            out[c]=pd.to_numeric(out[c],errors='coerce')
    return out


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--year',type=int,required=True)
    ap.add_argument('--raw',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    control=json.loads(TARGETS.read_text())
    game_ids=[int(x) for x in control['year_to_game_ids'][str(a.year)]]
    result={'year':a.year,'requested_games':game_ids,'games':[]}

    nba_path=a.raw/f'nbastats_{a.year}.csv'
    v3_path=a.raw/f'nbastatsv3_{a.year}.csv'
    cdn_path=a.raw/f'cdnnba_{a.year}.csv'
    nba=histio.normalize_nba(pd.read_csv(nba_path,low_memory=False)) if nba_path.exists() else pd.DataFrame()
    v3=normalize_v3(pd.read_csv(v3_path,low_memory=False)) if v3_path.exists() else pd.DataFrame()
    cdn=normalize_cdn(pd.read_csv(cdn_path,low_memory=False)) if cdn_path.exists() else pd.DataFrame()

    for gid in game_ids:
        row={'game_id':gid}
        if a.year >= 2020 and not cdn.empty:
            g=cdn[cdn.gameId.eq(gid)]
            if not g.empty:
                try:
                    lu=modern.reconstruct_game_lineups(g)
                    row.update({'status':'PASS_CDN','source':'cdnnba','players_with_seconds':len(lu.seconds),'repairs':lu.repairs})
                    result['games'].append(row); continue
                except Exception as exc:
                    row['cdn_error']=str(exc)
        legacy_game=nba[nba.GAME_ID.eq(gid)] if not nba.empty else pd.DataFrame()
        v3_game=v3[v3.gameId.eq(gid)] if not v3.empty else pd.DataFrame()
        row['legacy_rows']=int(len(legacy_game)); row['v3_rows']=int(len(v3_game))
        if legacy_game.empty:
            row.update({'status':'V3_ONLY_REQUIRED' if not v3_game.empty else 'SOURCE_MISSING'})
            result['games'].append(row); continue
        try:
            lu=v3engine.reconstruct_game_lineups(legacy_game,v3_game)
            row.update({'status':'PASS_V3_TEAM_LOCAL','source':'nbastats+nbastatsv3','players_with_seconds':len(lu.seconds),'repairs':lu.repairs})
        except Exception as exc:
            row.update({'status':'FAIL','error':str(exc)})
        result['games'].append(row)
    counts={}
    for r in result['games']: counts[r['status']]=counts.get(r['status'],0)+1
    result['status_counts']=counts
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({'year':a.year,'status_counts':counts,'games':[(r['game_id'],r['status']) for r in result['games']]},indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
