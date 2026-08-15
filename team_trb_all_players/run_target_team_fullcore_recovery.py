#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import rebuild_fullcore_treb_fanout as engine
import run_local_treb_production as io


def sid(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--targets', type=Path, required=True)
    ap.add_argument('--nba', type=Path, required=True)
    ap.add_argument('--v3', type=Path, required=True)
    ap.add_argument('--pbp', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()

    season = f"{args.year}-{(args.year + 1) % 100:02d}"
    targets = []
    with gzip.open(args.targets, 'rt', encoding='utf-8') as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if str(r.get('season')) == season and bool(r.get('full_core_reuse')):
                r['team_id'] = int(r['team_id'])
                r['player_id'] = sid(r['player_id'])
                targets.append(r)
    if not targets:
        raise RuntimeError(f'No targets for {season}')
    team_ids = sorted({int(r['team_id']) for r in targets})
    if len(team_ids) != 1:
        raise RuntimeError(f'Target-team wrapper requires exactly one team, got {team_ids}')
    target_team = team_ids[0]

    outdir = args.output_dir
    outdir.mkdir(parents=True, exist_ok=True)
    filt = outdir / 'filtered_sources'
    filt.mkdir(parents=True, exist_ok=True)

    nba_raw = pd.read_csv(args.nba, low_memory=False)
    nba_norm = io.normalize_nba(nba_raw.copy())
    target_games = []
    for gid, game in nba_norm.groupby('GAME_ID', sort=False):
        if target_team in engine.nba_game_teams(game):
            target_games.append(int(gid))
    target_games = sorted(set(target_games))
    if not target_games:
        raise RuntimeError(f'No NBA games found for target team {target_team} {season}')
    target_set = set(target_games)

    def filter_source(raw_path: Path, game_col: str, out_path: Path) -> tuple[int, int]:
        d = pd.read_csv(raw_path, low_memory=False)
        if game_col not in d.columns:
            raise RuntimeError(f'{raw_path} missing {game_col}')
        gids = pd.to_numeric(d[game_col], errors='coerce')
        keep = d[gids.isin(target_set)].copy()
        keep.to_csv(out_path, index=False)
        return len(d), len(keep)

    nba_gids = pd.to_numeric(nba_raw['GAME_ID'], errors='coerce')
    nba_keep = nba_raw[nba_gids.isin(target_set)].copy()
    nba_out = filt / f'nbastats_{args.year}.csv'
    nba_keep.to_csv(nba_out, index=False)
    v3_out = filt / f'nbastatsv3_{args.year}.csv'
    pbp_out = filt / f'pbpstats_{args.year}.csv'
    v3_total, v3_kept = filter_source(args.v3, 'gameId', v3_out)
    pbp_total, pbp_kept = filter_source(args.pbp, 'GAMEID', pbp_out)

    qa = {
        'status': 'PASS',
        'season': season,
        'target_team_id': target_team,
        'target_keys': len(targets),
        'selected_games': len(target_games),
        'first_game_id': target_games[0],
        'last_game_id': target_games[-1],
        'nba_rows_total': len(nba_raw),
        'nba_rows_kept': len(nba_keep),
        'v3_rows_total': v3_total,
        'v3_rows_kept': v3_kept,
        'pbp_rows_total': pbp_total,
        'pbp_rows_kept': pbp_kept,
    }
    (outdir / 'TARGET_TEAM_PRUNE_GATE.json').write_text(json.dumps(qa, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'event': 'TARGET_TEAM_PRUNE', **qa}), flush=True)

    cmd = [
        sys.executable, '-u', str(Path(__file__).with_name('rebuild_fullcore_treb_fanout.py')),
        '--year', str(args.year), '--targets', str(args.targets),
        '--nba', str(nba_out), '--v3', str(v3_out), '--pbp', str(pbp_out),
        '--output-dir', str(outdir),
    ]
    return subprocess.run(cmd, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
