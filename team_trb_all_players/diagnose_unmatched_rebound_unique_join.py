#!/usr/bin/env python3
"""Diagnose safe same-period unique-description fallback for TREB rebound joins.

The production join first requires text similarity plus temporal overlap. This
script measures whether rows that miss that window can nevertheless be mapped
unambiguously to exactly one same-period NBA event by normalized description.
No production logic is changed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core
import production_treb_engine as prod


def interval_gap(event_elapsed: float, start: float, end: float) -> float:
    lo, hi = sorted((float(start), float(end)))
    if lo <= event_elapsed <= hi:
        return 0.0
    return min(abs(event_elapsed - lo), abs(event_elapsed - hi))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--nba', type=Path, required=True)
    ap.add_argument('--pbp', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()

    nba = pd.read_csv(args.nba, low_memory=False)
    pbp = pd.read_csv(args.pbp, low_memory=False)
    nba['GAME_ID'] = pd.to_numeric(nba.GAME_ID, errors='raise').astype('int64')
    pbp['GAMEID'] = pd.to_numeric(pbp.GAMEID, errors='raise').astype('int64')

    rows = []
    total_rebounds = 0
    current_unmatched = 0
    exact_unique = 0
    fuzzy_unique = 0
    ambiguous = 0
    no_text_match = 0

    for game_id, pg0 in pbp.groupby('GAMEID', sort=False):
        ng = nba[nba.GAME_ID.eq(int(game_id))].copy()
        if ng.empty:
            continue
        ng = ng.sort_values(['PERIOD','EVENTNUM'], kind='stable').copy()
        ng['DESCRIPTION_NORM'] = core.nba_description(ng)
        ng['ELAPSED'] = [core.elapsed_seconds(int(p), c) for p,c in zip(ng.PERIOD, ng.PCTIMESTRING)]

        pg = pg0.copy()
        rebounds = pg[pg.DESCRIPTION.fillna('').str.contains('rebound', case=False)].copy()
        rebounds['DESCRIPTION_NORM'] = rebounds.DESCRIPTION.map(prod._norm)
        rebounds['START_ELAPSED'] = [core.elapsed_seconds(int(p), c) for p,c in zip(rebounds.PERIOD, rebounds.STARTTIME)]
        rebounds['END_ELAPSED'] = [core.elapsed_seconds(int(p), c) for p,c in zip(rebounds.PERIOD, rebounds.ENDTIME)]

        for _, row in rebounds.iterrows():
            total_rebounds += 1
            window = ng[(ng.PERIOD.eq(row.PERIOD)) &
                        (ng.ELAPSED.gt(row.START_ELAPSED - 5)) &
                        (ng.ELAPSED.lt(row.END_ELAPSED + 5))]
            acceptable = []
            for idx, cand in window.iterrows():
                dist = core._distance(row.DESCRIPTION_NORM, cand.DESCRIPTION_NORM)
                if dist < .2:
                    acceptable.append((dist, int(idx)))
            if acceptable:
                continue

            current_unmatched += 1
            period = ng[ng.PERIOD.eq(row.PERIOD)].copy()
            scored = []
            for idx, cand in period.iterrows():
                dist = core._distance(row.DESCRIPTION_NORM, cand.DESCRIPTION_NORM)
                if dist < .2:
                    scored.append({
                        'index': int(idx),
                        'event_num': int(cand.EVENTNUM),
                        'event_type': int(cand.EVENTMSGTYPE),
                        'event_action_type': int(cand.EVENTMSGACTIONTYPE),
                        'nba_description': str(cand.DESCRIPTION_NORM),
                        'distance': float(dist),
                        'elapsed': float(cand.ELAPSED),
                        'gap_seconds': float(interval_gap(cand.ELAPSED, row.START_ELAPSED, row.END_ELAPSED)),
                    })
            scored.sort(key=lambda x: (x['distance'], x['gap_seconds'], x['event_num']))
            exact = [x for x in scored if x['distance'] == 0]
            if len(exact) == 1:
                classification = 'exact_unique'
                exact_unique += 1
            elif len(scored) == 1:
                classification = 'fuzzy_unique'
                fuzzy_unique += 1
            elif scored:
                classification = 'ambiguous'
                ambiguous += 1
            else:
                classification = 'no_text_match'
                no_text_match += 1
            rows.append({
                'game_id': int(game_id),
                'period': int(row.PERIOD),
                'start_time': str(row.STARTTIME),
                'end_time': str(row.ENDTIME),
                'pbp_description': str(row.DESCRIPTION),
                'description_norm': str(row.DESCRIPTION_NORM),
                'classification': classification,
                'candidate_count': len(scored),
                'exact_candidate_count': len(exact),
                'candidates': scored[:12],
            })

    exact_gaps = [r['candidates'][0]['gap_seconds'] for r in rows if r['classification']=='exact_unique']
    fuzzy_gaps = [r['candidates'][0]['gap_seconds'] for r in rows if r['classification']=='fuzzy_unique']
    payload = {
        'total_rebound_rows': total_rebounds,
        'current_window_unmatched': current_unmatched,
        'exact_unique': exact_unique,
        'fuzzy_unique': fuzzy_unique,
        'ambiguous': ambiguous,
        'no_text_match': no_text_match,
        'exact_unique_max_gap_seconds': max(exact_gaps) if exact_gaps else None,
        'fuzzy_unique_max_gap_seconds': max(fuzzy_gaps) if fuzzy_gaps else None,
        'rows': rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k!='rows'}, indent=2))
    for r in rows[:80]:
        best = r['candidates'][0] if r['candidates'] else None
        print('UNMATCHED', r['game_id'], 'P', r['period'], r['classification'], r['pbp_description'], 'best=', best)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
