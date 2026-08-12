#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import math
import pandas as pd

import v3_only_lineups_2019 as engine

GAMES = [21901316, 21901317, 21901318]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--v3', type=Path, required=True)
    ap.add_argument('--cc0-json', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()

    v3 = pd.read_csv(a.v3, low_memory=False)
    v3['gameId'] = pd.to_numeric(v3.gameId, errors='coerce')
    cc0 = json.loads(a.cc0_json.read_text(encoding='utf-8'))
    cc0_rows = cc0.get('blocker_rows', [])
    by_game = {}
    for row in cc0_rows:
        gid = int(row['normalized_game_id'])
        if gid in GAMES:
            by_game.setdefault(gid, []).append(row)

    payload = {'status': 'COMPLETE', 'games': []}
    for gid in GAMES:
        g = v3[v3.gameId.eq(gid)].copy()
        row = {'game_id': gid, 'v3_rows': int(len(g))}
        try:
            lu = engine.reconstruct_game_lineups(g)
            row['status'] = 'PASS_LINEUP'
            row['players_with_seconds'] = len(lu.seconds)
            row['substitution_expansion_count'] = len(lu.substitution_expansion_audit)
            row['lineup_audit_count'] = len(lu.repairs)
            row['player_seconds'] = {str(pid): round(float(sec), 3) for pid, sec in sorted(lu.seconds.items())}

            comparisons = []
            for src in by_game.get(gid, []):
                pid = int(float(src['personId']))
                source = src.get('parsed_minutes')
                if source is None or (isinstance(source, float) and math.isnan(source)):
                    source_min = 0.0
                else:
                    source_min = float(source)
                engine_min = float(lu.seconds.get(pid, 0.0)) / 60.0
                comparisons.append({
                    'player_id': pid,
                    'player': ' '.join(str(src.get(k) or '').strip() for k in ('firstName','lastName')).strip(),
                    'source_minutes': source_min,
                    'engine_minutes': round(engine_min, 6),
                    'abs_delta_minutes': round(abs(engine_min-source_min), 6),
                })
            row['minute_comparisons'] = comparisons
            row['max_abs_delta_minutes'] = max((x['abs_delta_minutes'] for x in comparisons), default=None)
            row['sum_abs_delta_minutes'] = round(sum(x['abs_delta_minutes'] for x in comparisons), 6)
        except Exception as exc:
            row['status'] = 'FAIL_LINEUP'
            row['error'] = str(exc)
        payload['games'].append(row)

    payload['status_counts'] = {}
    for r in payload['games']:
        payload['status_counts'][r['status']] = payload['status_counts'].get(r['status'], 0) + 1
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'status_counts': payload['status_counts'],
        'games': [(r['game_id'], r['status'], r.get('max_abs_delta_minutes')) for r in payload['games']],
    }, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
