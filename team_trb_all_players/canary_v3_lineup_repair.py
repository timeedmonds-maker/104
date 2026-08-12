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
MAX_SEARCH_STATES = 1500


def normalize_v3(df: pd.DataFrame) -> pd.DataFrame:
    return v3engine.normalize_v3(df)


def normalize_cdn(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ('gameId', 'period', 'actionNumber', 'orderNumber', 'personId', 'teamId'):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')
    return out


def _state_signature(state: dict[tuple[int, int, int], tuple[int, ...]]) -> tuple:
    return tuple(sorted((key, tuple(value)) for key, value in state.items()))


def _repair_choices(state: dict[tuple[int, int, int], tuple[int, ...]]) -> list[dict]:
    return [
        {'game_id': k[0], 'period': k[1], 'team_id': k[2], 'starters': list(starters)}
        for k, starters in sorted(state.items())
    ]


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
    """Reproduce the *current* engine ambiguity and enumerate legal five-man starts.

    No game-specific starter candidates are stored here.  The only candidate
    universe is the one exposed by the current reconstruction failure itself,
    so later engine repairs cannot silently leave this diagnostic search stale.
    """
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
    prepared['ELAPSED'] = [
        v3engine.core.elapsed_seconds(int(p), c)
        for p, c in zip(prepared.PERIOD, prepared.PCTIMESTRING)
    ]
    order_map = v3engine._v3_action_map(v3_game)
    prepared['V3_ORDER'] = [
        order_map.get((int(p), int(ev)), 10_000_000 + int(ev))
        for p, ev in zip(prepared.PERIOD, prepared.EVENTNUM)
    ]
    period = prepared[prepared.PERIOD.eq(period_number)].sort_values(
        ['ELAPSED', 'V3_ORDER', 'EVENTNUM'], kind='stable'
    )
    player_team = v3engine.core._player_team(prepared)

    pool = set(candidates) | set(prior)
    if len(pool) < 5:
        return None
    if len(candidates) < 5:
        combos = [
            set(c) for c in combinations(sorted(pool), 5)
            if set(candidates).issubset(c)
        ]
    else:
        combos = [set(c) for c in combinations(sorted(candidates), 5)]

    solutions = []
    for combo in combos:
        legal, violations = v3engine._simulate_team(period, team_id, combo, player_team)
        if legal and not violations:
            solutions.append(tuple(sorted(combo)))
    solutions = sorted(set(solutions))
    if not solutions:
        return None
    return (game_id, period_number, team_id), solutions


def search_full_game_repairs(legacy_game: pd.DataFrame, v3_game: pd.DataFrame, gid: int) -> dict:
    """Explore only source-driven ambiguous period starts until the whole game is legal.

    The search begins with zero overrides. Each reconstruction failure either:
    - exposes a current non-unique period-opening state, which is expanded into
      only the legal five-man solutions for that exact period/team; or
    - is a non-starter structural defect (missing transition, malformed sub,
      missing source, etc.), which is recorded as a terminal blocker.
    """
    queue: list[dict[tuple[int, int, int], tuple[int, ...]]] = [{}]
    seen = set()
    successes = []
    terminal_failures = []
    explored = 0
    first_error = None
    ambiguity_nodes = 0

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
                'repair_choices': _repair_choices(state),
                'player_seconds': {str(pid): int(sec) for pid, sec in sorted(lu.seconds.items())},
                'players_with_seconds': int(len(lu.seconds)),
            })
            continue
        except Exception as exc:
            error = str(exc)
            if first_error is None:
                first_error = error

        nxt = _next_ambiguity(error, legacy_game, v3_game)
        if nxt is None:
            terminal_failures.append({
                'repair_choices': _repair_choices(state),
                'error': error,
            })
            continue

        key, solutions = nxt
        ambiguity_nodes += 1
        if key in state:
            terminal_failures.append({
                'repair_choices': _repair_choices(state),
                'error': error,
                'diagnostic': 'same ambiguity key reappeared after explicit override',
            })
            continue

        for solution in solutions:
            branch = dict(state)
            branch[key] = tuple(solution)
            queue.append(branch)

    return {
        'method': 'dynamic_current_engine_ambiguity_search',
        'game_id': int(gid),
        'first_error': first_error,
        'states_explored': explored,
        'ambiguity_nodes_expanded': ambiguity_nodes,
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
                        'status': 'PASS_CDN',
                        'source': 'cdnnba',
                        'players_with_seconds': len(lu.seconds),
                        'repairs': lu.repairs,
                        'player_seconds': {
                            str(pid): int(round(sec)) for pid, sec in sorted(lu.seconds.items())
                        },
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
                'status': 'PASS_V3_TEAM_LOCAL',
                'source': 'nbastats+nbastatsv3',
                'players_with_seconds': len(lu.seconds),
                'repairs': lu.repairs,
                'player_seconds': {str(pid): int(sec) for pid, sec in sorted(lu.seconds.items())},
            })
        except Exception as exc:
            row.update({'status': 'FAIL', 'error': str(exc)})
            row['repair_search'] = search_full_game_repairs(legacy_game, v3_game, gid)
        result['games'].append(row)

    counts = {}
    for r in result['games']:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    result['status_counts'] = counts
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({
        'year': a.year,
        'status_counts': counts,
        'games': [
            (
                r['game_id'],
                r['status'],
                r.get('repair_search', {}).get('full_game_solution_count')
                if isinstance(r.get('repair_search'), dict) else None,
                r.get('repair_search', {}).get('search_complete')
                if isinstance(r.get('repair_search'), dict) else None,
            )
            for r in result['games']
        ],
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
