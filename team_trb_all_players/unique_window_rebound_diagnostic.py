#!/usr/bin/env python3
"""Diagnose the post-exact-identity unmatched rebound tail.

No production data are changed here.  For each still-unmatched PBP Stats rebound,
reserve every NBA event already consumed by the validated production join and ask
whether exactly one unused NBA EVENTMSGTYPE=4 row remains inside that PBP
possession window.  Also back-test the same uniqueness condition on already
matched rows; a wrong forced match is a hard veto against promotion.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

import pandas as pd

import local_treb_rebuild as core
import production_rebound_v2 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io


def game_start_year(game_id: int) -> int:
    prefix = int(str(int(game_id)).zfill(8)[:3])
    return 2000 + (prefix - 200)


def norm(x: object) -> str:
    if pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip().lower()


def rebound_rows(pbp_game: pd.DataFrame) -> pd.DataFrame:
    ordered = pbp_game.copy()
    ordered["PREV_PBP_DESCRIPTION"] = ordered.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    rows = ordered[ordered.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    rows["DESCRIPTION_NORM"] = rows.DESCRIPTION.map(norm)
    rows["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rows.PERIOD, rows.STARTTIME)]
    rows["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rows.PERIOD, rows.ENDTIME)]
    return rows


def window_candidates(nba: pd.DataFrame, row: pd.Series, used_other: set[int], alpha: int = 5) -> pd.DataFrame:
    return nba[
        nba.PERIOD.eq(row.PERIOD)
        & nba.EVENTMSGTYPE.eq(4)
        & nba.ELAPSED.gt(int(row.START_ELAPSED) - alpha)
        & nba.ELAPSED.lt(int(row.END_ELAPSED) + alpha)
        & ~nba.index.isin(used_other)
    ].copy()


def candidate_payload(nba: pd.DataFrame, candidates: pd.DataFrame, pbp_desc: str) -> list[dict]:
    out=[]
    for idx, r in candidates.sort_values(["ELAPSED","EVENTNUM"], kind="stable").iterrows():
        out.append({
            "nba_index": int(idx),
            "eventnum": int(r.EVENTNUM),
            "elapsed": int(r.ELAPSED),
            "description": str(r.DESCRIPTION_NORM),
            "distance": float(core._distance(norm(pbp_desc), str(r.DESCRIPTION_NORM))),
            "player1_id": int(r.PLAYER1_ID) if pd.notna(r.PLAYER1_ID) else 0,
            "action_type": int(r.EVENTMSGACTIONTYPE),
            "nba_is_real_rebound": bool(core._nba_real_rebound(nba, int(idx))),
            "lineup": [int(x) for x in r.LINEUP],
        })
    return out


def invariant_lineup_evidence(nba: pd.DataFrame, row: pd.Series) -> dict:
    lo=int(row.START_ELAPSED); hi=int(row.END_ELAPSED)
    if hi < lo: lo,hi=hi,lo
    span=nba[nba.PERIOD.eq(row.PERIOD) & nba.ELAPSED.ge(lo) & nba.ELAPSED.le(hi)]
    subs=span[span.EVENTMSGTYPE.eq(8)]
    lineups={tuple(int(x) for x in lineup) for lineup in span.LINEUP} if len(span) else set()
    return {
        "events_in_interval": int(len(span)),
        "substitutions_in_interval": int(len(subs)),
        "unique_lineups_in_interval": int(len(lineups)),
        "invariant_lineup": bool(len(span) > 0 and len(subs) == 0 and len(lineups) == 1),
        "lineup": list(next(iter(lineups))) if len(lineups)==1 else None,
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--nba', type=Path, required=True)
    ap.add_argument('--v3', type=Path, required=True)
    ap.add_argument('--pbp', type=Path, required=True)
    ap.add_argument('--summary', type=Path, required=True)
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args=ap.parse_args()

    summary=json.loads(args.summary.read_text())
    targets=sorted(int(g['game_id']) for g in summary['all_games']
                   if g.get('status')=='UNKNOWN_OR_REAL' and game_start_year(int(g['game_id']))==args.year)
    nba=io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3=lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp=io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
    nba_groups={int(g):f.copy() for g,f in nba[nba.GAME_ID.isin(targets)].groupby('GAME_ID',sort=False)}
    v3_groups={int(g):f.copy() for g,f in v3[v3.gameId.isin(targets)].groupby('gameId',sort=False)}
    pbp_groups={int(g):f.copy() for g,f in pbp[pbp.GAMEID.isin(targets)].groupby('GAMEID',sort=False)}

    games=[]; control_applicable=control_correct=control_wrong=control_duplicate_skipped=0
    for gid in targets:
        try:
            lineups=lineup_engine.reconstruct_game_lineups(nba_groups[gid], v3_groups[gid])
            joined,audit=rebound.join_pbp_rebounds(lineups,pbp_groups[gid])
            rows=rebound_rows(pbp_groups[gid])
            nba_ev=lineups.events
            assignment_counts=Counter(int(x) for x in joined.NBA_INDEX.dropna().astype(int))
            joined_idx=set(joined.index)
            used=set(assignment_counts)

            # Back-test only independent one-to-one legacy matches. If the legacy
            # join reused an NBA event, that row cannot serve as ground truth for
            # a one-to-one uniqueness test and is explicitly excluded.
            for idx,jrow in joined.iterrows():
                actual=int(jrow.NBA_INDEX)
                if assignment_counts[actual] != 1:
                    control_duplicate_skipped += 1
                    continue
                used_other=used-{actual}
                src=rows.loc[idx]
                cands=window_candidates(nba_ev,src,used_other)
                if len(cands)==1:
                    control_applicable += 1
                    forced=int(cands.index[0])
                    if forced==actual: control_correct += 1
                    else: control_wrong += 1

            unmatched=[]
            for idx,row in rows.loc[~rows.index.isin(joined_idx)].iterrows():
                cands=window_candidates(nba_ev,row,used)
                invariant=invariant_lineup_evidence(nba_ev,row)
                unmatched.append({
                    "pbp_index": int(idx),
                    "period": int(row.PERIOD),
                    "start_time": str(row.STARTTIME),
                    "end_time": str(row.ENDTIME),
                    "description": str(row.DESCRIPTION),
                    "previous_description": str(row.PREV_PBP_DESCRIPTION) if pd.notna(row.PREV_PBP_DESCRIPTION) else "",
                    "unique_unused_window_rebound": bool(len(cands)==1),
                    "unused_window_rebound_count": int(len(cands)),
                    "candidates": candidate_payload(nba_ev,cands,str(row.DESCRIPTION)),
                    "invariant_lineup_evidence": invariant,
                })
            games.append({
                "game_id":gid,
                "status":"OK",
                "unmatched_rows":len(unmatched),
                "unique_window_rows":sum(x['unique_unused_window_rebound'] for x in unmatched),
                "all_unmatched_unique_window":bool(unmatched and all(x['unique_unused_window_rebound'] for x in unmatched)),
                "all_unmatched_invariant_lineup":bool(unmatched and all(x['invariant_lineup_evidence']['invariant_lineup'] for x in unmatched)),
                "join_audit":audit,
                "detail":unmatched,
            })
        except Exception as exc:
            games.append({"game_id":gid,"status":"ERROR","error":f"{type(exc).__name__}: {exc}"})

    ok=[g for g in games if g['status']=='OK']
    out={
        "year":args.year,
        "target_games":len(targets),
        "games_ok":len(ok),
        "games_error":len(games)-len(ok),
        "unmatched_rows":sum(g.get('unmatched_rows',0) for g in ok),
        "unique_window_rows":sum(g.get('unique_window_rows',0) for g in ok),
        "games_all_unmatched_unique_window":sum(g.get('all_unmatched_unique_window',False) for g in ok),
        "games_all_unmatched_invariant_lineup":sum(g.get('all_unmatched_invariant_lineup',False) for g in ok),
        "control_applicable":control_applicable,
        "control_correct":control_correct,
        "control_wrong":control_wrong,
        "control_duplicate_skipped":control_duplicate_skipped,
        "games":games,
    }
    args.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k!='games'},indent=2),flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
