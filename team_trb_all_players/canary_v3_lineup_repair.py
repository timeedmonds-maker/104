#!/usr/bin/env python3
from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path

import pandas as pd

import run_local_treb_production as histio
import production_treb_engine_v3 as v3engine
import modern_cdn_lineups as modern

BASE = Path(__file__).resolve().parent
TARGETS = BASE / 'final_integrity_rebuild' / 'EXCLUDED_GAME_REPAIR_TARGETS.json'

# Ambiguities remaining after V3 chronology/team-local carry.  Trialing is
# deliberately explicit and game-period-team keyed.  No generic lineup rule is
# relaxed.  A trial must survive full-game reconstruction before its seconds are
# recorded for comparison with independent CC0 player-minute evidence.
AMBIGUOUS_TRIALS = {
    20201160: {'period': 5, 'team_id': 1610612765, 'fixed': [361, 688, 1497, 2246], 'variable': [1088, 1442, 1888]},
    20400335: {'period': 2, 'team_id': 1610612740, 'fixed': [1924, 2365, 2424, 2437, 2454], 'variable': []},
    20600887: {'period': 5, 'team_id': 1610612750, 'fixed': [1729, 201147, 201567, 200826], 'variable': [1536, 2033]},
    20700319: {'period': 4, 'team_id': 1610612752, 'fixed': [2446, 2546, 2754, 201163], 'variable': [255, 1897, 2756, 101181]},
    20800142: {'period': 5, 'team_id': 1610612752, 'fixed': [2037, 2047, 2768, 201163], 'variable': [2216, 200776]},
    21100842: {'period': 2, 'team_id': 1610612766, 'fixed': [101107, 201150, 201946, 201974], 'variable': [2550, 2736]},
    21500916: {'period': 5, 'team_id': 1610612757, 'fixed': [], 'variable': [202334, 203148, 203459, 203943, 203994, 1626145, 1626192, 1626242]},
    21800143: {'period': 6, 'team_id': 1610612741, 'fixed': [201577, 203897, 203953, 1626166], 'variable': [202703, 203487, 203200, 1627885]},
    22000485: {'period': 1, 'team_id': 1610612742, 'fixed': [201599, 202710, 203083, 203939], 'variable': [1628973, 1630179, 201144]},
}


def normalize_v3(df: pd.DataFrame) -> pd.DataFrame:
    return v3engine.normalize_v3(df)


def normalize_cdn(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ('gameId', 'period', 'actionNumber', 'orderNumber', 'personId', 'teamId'):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')
    return out


def trial_combos(spec: dict) -> list[tuple[int, ...]]:
    fixed = tuple(int(x) for x in spec['fixed'])
    variable = tuple(int(x) for x in spec['variable'])
    need = 5 - len(fixed)
    if need < 0:
        return []
    if need == 0:
        return [tuple(sorted(fixed))]
    return [tuple(sorted(fixed + choice)) for choice in combinations(variable, need)]


def enumerate_full_game_trials(legacy_game: pd.DataFrame, v3_game: pd.DataFrame, gid: int) -> dict | None:
    spec = AMBIGUOUS_TRIALS.get(int(gid))
    if spec is None or legacy_game.empty:
        return None
    key = (int(gid), int(spec['period']), int(spec['team_id']))
    repair_map = v3engine.legacy.core.STARTER_REPAIRS
    previous = repair_map.get(key)
    candidates_of_interest = sorted(set(int(x) for x in spec['fixed'] + spec['variable']))
    successful = []
    failures = []
    try:
        for combo in trial_combos(spec):
            repair_map[key] = list(combo)
            try:
                lu = v3engine.reconstruct_game_lineups(legacy_game, v3_game)
                successful.append({
                    'starters': list(combo),
                    'candidate_seconds': {str(pid): int(lu.seconds.get(pid, 0)) for pid in candidates_of_interest},
                    'players_with_seconds': int(len(lu.seconds)),
                })
            except Exception as exc:
                failures.append({'starters': list(combo), 'error': str(exc)})
    finally:
        if previous is None:
            repair_map.pop(key, None)
        else:
            repair_map[key] = previous
    return {
        'period': int(spec['period']),
        'team_id': int(spec['team_id']),
        'fixed': [int(x) for x in spec['fixed']],
        'variable': [int(x) for x in spec['variable']],
        'trials_attempted': len(successful) + len(failures),
        'full_game_successes': successful,
        'full_game_failures': failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--raw', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    control = json.loads(TARGETS.read_text())
    game_ids = [int(x) for x in control['year_to_game_ids'][str(a.year)]]
    result = {'year': a.year, 'requested_games': game_ids, 'games': []}

    nba_path = a.raw / f'nbastats_{a.year}.csv'
    v3_path = a.raw / f'nbastatsv3_{a.year}.csv'
    cdn_path = a.raw / f'cdnnba_{a.year}.csv'
    nba = histio.normalize_nba(pd.read_csv(nba_path, low_memory=False)) if nba_path.exists() else pd.DataFrame()
    v3 = normalize_v3(pd.read_csv(v3_path, low_memory=False)) if v3_path.exists() else pd.DataFrame()
    cdn = normalize_cdn(pd.read_csv(cdn_path, low_memory=False)) if cdn_path.exists() else pd.DataFrame()

    for gid in game_ids:
        row = {'game_id': gid}
        if a.year >= 2020 and not cdn.empty:
            g = cdn[cdn.gameId.eq(gid)]
            if not g.empty:
                try:
                    lu = modern.reconstruct_game_lineups(g)
                    row.update({'status': 'PASS_CDN', 'source': 'cdnnba', 'players_with_seconds': len(lu.seconds), 'repairs': lu.repairs})
                    result['games'].append(row)
                    continue
                except Exception as exc:
                    row['cdn_error'] = str(exc)
        legacy_game = nba[nba.GAME_ID.eq(gid)] if not nba.empty else pd.DataFrame()
        v3_game = v3[v3.gameId.eq(gid)] if not v3.empty else pd.DataFrame()
        row['legacy_rows'] = int(len(legacy_game))
        row['v3_rows'] = int(len(v3_game))
        if legacy_game.empty:
            row.update({'status': 'V3_ONLY_REQUIRED' if not v3_game.empty else 'SOURCE_MISSING'})
            result['games'].append(row)
            continue
        try:
            lu = v3engine.reconstruct_game_lineups(legacy_game, v3_game)
            row.update({'status': 'PASS_V3_TEAM_LOCAL', 'source': 'nbastats+nbastatsv3', 'players_with_seconds': len(lu.seconds), 'repairs': lu.repairs})
        except Exception as exc:
            row.update({'status': 'FAIL', 'error': str(exc)})
            trials = enumerate_full_game_trials(legacy_game, v3_game, gid)
            if trials is not None:
                row['explicit_starter_trials'] = trials
        result['games'].append(row)
    counts = {}
    for r in result['games']:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    result['status_counts'] = counts
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'year': a.year, 'status_counts': counts, 'games': [(r['game_id'], r['status']) for r in result['games']]}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
