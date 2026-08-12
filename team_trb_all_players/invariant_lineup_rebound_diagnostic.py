#!/usr/bin/env python3
"""Test a TREB-specific fallback for unmatched credited player rebounds.

PBP Stats defines the rebound universe.  If a player-credited rebound cannot be
joined to an NBA rebound row but the reconstructed NBA lineup is identical for
the entire PBP possession interval, exact NBA rebound identity is unnecessary
for TREB attribution: the PBP row supplies the rebound and the invariant NBA
interval supplies the ten players on court.

This script is diagnostic only.  It back-tests the inferred interval lineup
against already-matched rebound rows before reporting any promotion candidates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import pandas as pd

import local_treb_rebuild as core
import production_rebound_v2 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

COUNTER_RE = re.compile(r"\(\s*off\s*:\s*\d+\s+def\s*:\s*\d+\s*\)", re.I)


def game_start_year(game_id: int) -> int:
    prefix=int(str(int(game_id)).zfill(8)[:3])
    return 2000 + prefix - 200


def rebound_rows(pbp_game: pd.DataFrame) -> pd.DataFrame:
    ordered=pbp_game.copy()
    ordered['PREV_PBP_DESCRIPTION']=ordered.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    rows=ordered[ordered.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    rows['START_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rows.PERIOD,rows.STARTTIME)]
    rows['END_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rows.PERIOD,rows.ENDTIME)]
    return rows


def infer_interval_lineup(nba: pd.DataFrame,row: pd.Series) -> dict:
    lo=int(row.START_ELAPSED); hi=int(row.END_ELAPSED)
    if hi < lo: lo,hi=hi,lo
    # Include both interval boundaries. A substitution exactly on either boundary
    # makes the attribution ambiguous and therefore vetoes the fallback.
    span=nba[nba.PERIOD.eq(row.PERIOD) & nba.ELAPSED.ge(lo) & nba.ELAPSED.le(hi)]
    subs=span[span.EVENTMSGTYPE.eq(8)]
    lineups={tuple(int(x) for x in lineup) for lineup in span.LINEUP} if len(span) else set()
    invariant=bool(len(span)>0 and len(subs)==0 and len(lineups)==1)
    return {
        'invariant':invariant,
        'events':int(len(span)),
        'substitutions':int(len(subs)),
        'unique_lineups':int(len(lineups)),
        'lineup':list(next(iter(lineups))) if len(lineups)==1 else None,
    }


def is_player_credited(description: object) -> bool:
    return bool(COUNTER_RE.search(str(description)))


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--nba',type=Path,required=True)
    ap.add_argument('--v3',type=Path,required=True)
    ap.add_argument('--pbp',type=Path,required=True)
    ap.add_argument('--summary',type=Path,required=True)
    ap.add_argument('--year',type=int,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()

    summary=json.loads(args.summary.read_text())
    targets=sorted(int(g['game_id']) for g in summary['all_games']
                   if g.get('status')=='UNKNOWN_OR_REAL' and game_start_year(int(g['game_id']))==args.year)
    nba=io.normalize_nba(pd.read_csv(args.nba,low_memory=False))
    v3=lineup_engine.normalize_v3(pd.read_csv(args.v3,low_memory=False))
    pbp=io.normalize_pbp(pd.read_csv(args.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba[nba.GAME_ID.isin(targets)].groupby('GAME_ID',sort=False)}
    vg={int(g):f.copy() for g,f in v3[v3.gameId.isin(targets)].groupby('gameId',sort=False)}
    pg={int(g):f.copy() for g,f in pbp[pbp.GAMEID.isin(targets)].groupby('GAMEID',sort=False)}

    controls=controls_correct=controls_wrong=0
    target_player_rows=target_player_invariant_rows=target_generic_rows=target_generic_invariant_rows=0
    games=[]
    for gid in targets:
        try:
            lineups=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid])
            joined,audit=rebound.join_pbp_rebounds(lineups,pg[gid])
            rows=rebound_rows(pg[gid]); joined_idx=set(joined.index)

            for idx,jrow in joined.iterrows():
                src=rows.loc[idx]
                if not is_player_credited(src.DESCRIPTION):
                    continue
                inf=infer_interval_lineup(lineups.events,src)
                if not inf['invariant']:
                    continue
                controls += 1
                if tuple(inf['lineup']) == tuple(int(x) for x in jrow.LINEUP): controls_correct += 1
                else: controls_wrong += 1

            detail=[]
            for idx,row in rows.loc[~rows.index.isin(joined_idx)].iterrows():
                credited=is_player_credited(row.DESCRIPTION)
                inf=infer_interval_lineup(lineups.events,row)
                if credited:
                    target_player_rows += 1
                    target_player_invariant_rows += int(inf['invariant'])
                else:
                    target_generic_rows += 1
                    target_generic_invariant_rows += int(inf['invariant'])
                detail.append({
                    'pbp_index':int(idx),'period':int(row.PERIOD),
                    'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),
                    'description':str(row.DESCRIPTION),'player_credited':credited,
                    'interval':inf,
                    'safe_player_invariant_candidate':bool(credited and inf['invariant']),
                })
            safe=sum(x['safe_player_invariant_candidate'] for x in detail)
            games.append({
                'game_id':gid,'status':'OK','unmatched_rows':len(detail),
                'safe_player_invariant_rows':safe,
                'all_unmatched_safe_player_invariant':bool(detail and safe==len(detail)),
                'detail':detail,'join_audit':audit,
            })
        except Exception as exc:
            games.append({'game_id':gid,'status':'ERROR','error':f'{type(exc).__name__}: {exc}'})

    ok=[g for g in games if g.get('status')=='OK']
    out={
        'year':args.year,'target_games':len(targets),'games_ok':len(ok),'games_error':len(games)-len(ok),
        'unmatched_rows':sum(g.get('unmatched_rows',0) for g in ok),
        'player_credited_rows':target_player_rows,
        'player_credited_invariant_rows':target_player_invariant_rows,
        'generic_rows':target_generic_rows,
        'generic_invariant_rows':target_generic_invariant_rows,
        'games_all_unmatched_safe_player_invariant':sum(g.get('all_unmatched_safe_player_invariant',False) for g in ok),
        'control_applicable':controls,'control_correct':controls_correct,'control_wrong':controls_wrong,
        'games':games,
    }
    args.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k!='games'},indent=2),flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
