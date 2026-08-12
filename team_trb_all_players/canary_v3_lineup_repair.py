#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from itertools import combinations
import json
from pathlib import Path
import re

import pandas as pd

import run_local_treb_production as histio
import production_treb_engine_v3 as v3engine
import modern_cdn_lineups as modern

BASE = Path(__file__).resolve().parent
TARGETS = BASE / 'final_integrity_rebuild' / 'EXCLUDED_GAME_REPAIR_TARGETS.json'

# First ambiguity exposed by the current diagnostic engine for each unresolved
# game.  These complete pools seed a recursive search.  Once one choice is
# supplied, later ambiguities are discovered and branched automatically.
AMBIGUOUS_TRIALS = {
    20201160: {'period': 5, 'team_id': 1610612765, 'fixed': [361, 688, 1497, 2246], 'variable': [1088, 1442, 1888]},
    20600887: {'period': 5, 'team_id': 1610612750, 'fixed': [1729, 201147, 201567, 200826], 'variable': [1536, 2033]},
    20700319: {'period': 4, 'team_id': 1610612752, 'fixed': [2446, 2546, 2754, 201163], 'variable': [255, 1897, 2756, 101181]},
    20800142: {'period': 5, 'team_id': 1610612752, 'fixed': [2037, 2047, 2768, 201163], 'variable': [2216, 200776]},
    21100842: {'period': 2, 'team_id': 1610612766, 'fixed': [101107, 201150, 201946, 201974], 'variable': [2550, 2736]},
    21500916: {'period': 5, 'team_id': 1610612757, 'fixed': [], 'variable': [202334, 203148, 203459, 203943, 203994, 1626145, 1626192, 1626242]},
    21800143: {'period': 6, 'team_id': 1610612741, 'fixed': [201577, 203897, 203953, 1626166], 'variable': [202703, 203487, 203200, 1627885]},
    22000485: {'period': 1, 'team_id': 1610612742, 'fixed': [201599, 202710, 203083, 203939], 'variable': [1628973, 1630179, 201144]},
}
MISSING_TRANSITION_GAME = 20400335
MAX_SEARCH_STATES = 2500


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


def _state_signature(state: dict[tuple[int, int, int], tuple[int, ...]]) -> tuple:
    return tuple(sorted((key, tuple(value)) for key, value in state.items()))


def _with_repairs_reconstruct(legacy_game: pd.DataFrame, v3_game: pd.DataFrame, state: dict):
    repair_map = v3engine.legacy.core.STARTER_REPAIRS
    previous = {k: repair_map.get(k) for k in state}
    try:
        for key, starters in state.items():
            repair_map[key] = list(starters)
        return v3engine.reconstruct_game_lineups(legacy_game, v3_game)
    finally:
        for key, value in previous.items():
            if value is None:
                repair_map.pop(key, None)
            else:
                repair_map[key] = value


def _parse_list(error: str, label: str) -> list[int] | None:
    m = re.search(rf'{re.escape(label)}=(\[[^\]]*\])', error)
    if not m:
        return None
    try:
        return [int(x) for x in ast.literal_eval(m.group(1))]
    except Exception:
        return None


def _next_ambiguity(error: str, legacy_game: pd.DataFrame, v3_game: pd.DataFrame):
    m = re.search(r'non-unique v3/team-local starter solution game=(\d+) period=(\d+) team=(\d+):', error)
    if not m:
        return None
    game_id, period_number, team_id = map(int, m.groups())
    candidates = _parse_list(error, 'candidates')
    prior = _parse_list(error, 'prior') or []
    if candidates is None:
        return None

    prepared, _ = v3engine.legacy.prepare_nba_game(legacy_game)
    prepared = prepared.copy()
    prepared['DESCRIPTION_NORM'] = v3engine.core.nba_description(prepared)
    prepared['ELAPSED'] = [v3engine.core.elapsed_seconds(int(p), c) for p, c in zip(prepared.PERIOD, prepared.PCTIMESTRING)]
    order_map = v3engine._v3_action_map(v3_game)
    prepared['V3_ORDER'] = [order_map.get((int(p), int(ev)), 10_000_000 + int(ev)) for p, ev in zip(prepared.PERIOD, prepared.EVENTNUM)]
    period = prepared[prepared.PERIOD.eq(period_number)].sort_values(['ELAPSED', 'V3_ORDER', 'EVENTNUM'], kind='stable')
    player_team = v3engine.core._player_team(prepared)

    pool = set(candidates) | set(prior)
    if len(pool) < 5:
        return None
    if len(candidates) < 5:
        combos = [set(c) for c in combinations(sorted(pool), 5) if set(candidates).issubset(c)]
    else:
        combos = [set(c) for c in combinations(sorted(candidates), 5)]
    evaluated = []
    for combo in combos:
        legal, violations = v3engine._simulate_team(period, team_id, combo, player_team)
        if legal:
            evaluated.append((len(violations), tuple(sorted(combo))))
    if not evaluated:
        return None
    best_score = min(x[0] for x in evaluated)
    solutions = sorted({x[1] for x in evaluated if x[0] == best_score})
    if best_score != 0:
        return None
    return (game_id, period_number, team_id), solutions


def search_full_game_repairs(legacy_game: pd.DataFrame, v3_game: pd.DataFrame, gid: int) -> dict | None:
    spec = AMBIGUOUS_TRIALS.get(int(gid))
    if spec is None:
        return None
    first_key = (int(gid), int(spec['period']), int(spec['team_id']))
    queue = [{first_key: combo} for combo in trial_combos(spec)]
    seen = set()
    successes = []
    terminal_failures = []
    explored = 0

    while queue and explored < MAX_SEARCH_STATES:
        state = queue.pop(0)
        sig = _state_signature(state)
        if sig in seen:
            continue
        seen.add(sig)
        explored += 1
        try:
            lu = _with_repairs_reconstruct(legacy_game, v3_game, state)
            successes.append({
                'repair_choices': [
                    {'game_id': k[0], 'period': k[1], 'team_id': k[2], 'starters': list(starters)}
                    for k, starters in sorted(state.items())
                ],
                'player_seconds': {str(pid): int(sec) for pid, sec in sorted(lu.seconds.items())},
                'players_with_seconds': int(len(lu.seconds)),
            })
            continue
        except Exception as exc:
            error = str(exc)
        nxt = _next_ambiguity(error, legacy_game, v3_game)
        if nxt is not None:
            key, solutions = nxt
            if key in state:
                terminal_failures.append({'repair_choices': [list(x) for x in sig], 'error': error})
                continue
            for solution in solutions:
                branch = dict(state)
                branch[key] = tuple(solution)
                queue.append(branch)
        else:
            terminal_failures.append({
                'repair_choices': [
                    {'game_id': k[0], 'period': k[1], 'team_id': k[2], 'starters': list(starters)}
                    for k, starters in sorted(state.items())
                ],
                'error': error,
            })

    return {
        'initial_period': int(spec['period']),
        'initial_team_id': int(spec['team_id']),
        'states_explored': explored,
        'state_limit': MAX_SEARCH_STATES,
        'queue_remaining_at_stop': len(queue),
        'full_game_solution_count': len(successes),
        'full_game_solutions': successes,
        'terminal_failure_count': len(terminal_failures),
        'terminal_failures': terminal_failures[:100],
        'search_complete': not queue,
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
                    row.update({
                        'status': 'PASS_CDN', 'source': 'cdnnba',
                        'players_with_seconds': len(lu.seconds), 'repairs': lu.repairs,
                        'player_seconds': {str(pid): int(round(sec)) for pid, sec in sorted(lu.seconds.items())},
                    })
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
            row.update({
                'status': 'PASS_V3_TEAM_LOCAL', 'source': 'nbastats+nbastatsv3',
                'players_with_seconds': len(lu.seconds), 'repairs': lu.repairs,
                'player_seconds': {str(pid): int(sec) for pid, sec in sorted(lu.seconds.items())},
            })
        except Exception as exc:
            row.update({'status': 'FAIL', 'error': str(exc)})
            if gid == MISSING_TRANSITION_GAME:
                row['repair_search'] = {
                    'status': 'MISSING_IN_PERIOD_TRANSITION_BLOCKER',
                    'reason': 'Explicit starter override would hide the participant violation and is therefore not a valid repair.',
                }
            else:
                search = search_full_game_repairs(legacy_game, v3_game, gid)
                if search is not None:
                    row['repair_search'] = search
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
