#!/usr/bin/env python3
"""V10 diagnostic audit for source-only team-credit rebound lineup anchors."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rebound_v5_source_only_audit as a
import production_rebound_v8 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
import local_treb_rebuild as core

TEAM_WORDS = {
    '76ERS','BOBCATS','BUCKS','BULLS','CAVALIERS','CELTICS','CLIPPERS','GRIZZLIES',
    'HAWKS','HEAT','HORNETS','JAZZ','KINGS','KNICKS','LAKERS','MAGIC','MAVERICKS',
    'NETS','NUGGETS','PACERS','PELICANS','PISTONS','RAPTORS','ROCKETS','SPURS','SUNS',
    'SUPERSONICS','THUNDER','TIMBERWOLVES','TRAIL BLAZERS','WARRIORS','WIZARDS'
}

def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Za-z0-9 ]+', ' ', str(s)).strip()).upper()

def team_credit(desc: str) -> bool:
    if re.match(r'^\s*\[[A-Za-z]{2,4}\]', str(desc)):
        return False
    m = re.match(r'^\s*(.*?)\s+REBOUND\b', str(desc), re.I)
    if not m:
        return False
    return _norm(m.group(1)) in TEAM_WORDS

def rules(events, row, exclude=None):
    if not team_credit(str(row.DESCRIPTION)):
        return {}
    lp = a.lineup_predictions(events, row, exclude)
    pm, ep, ci = lp['prior_miss_exact'], lp['endpoint_gap0'], lp['clock_invariant']
    out = {}
    if pm is not None:
        out['team_credit_prior_miss_exact'] = pm
    if ep is not None and ci is not None and ep == ci:
        out['team_credit_endpoint_clock_consensus'] = ep
    if pm is not None and ep is not None and ci is not None and pm == ep == ci:
        out['team_credit_prior_miss_endpoint_clock_agree'] = pm
    return out

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--year', type=int, required=True)
    p.add_argument('--games', required=True)
    p.add_argument('--chunk-id', required=True)
    p.add_argument('--nba', type=Path, required=True)
    p.add_argument('--v3', type=Path, required=True)
    p.add_argument('--pbp', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    z = p.parse_args()
    ids = [int(x) for x in z.games.split(',') if x]
    nba = io.normalize_nba(pd.read_csv(z.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(z.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(z.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby('GAME_ID', sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby('gameId', sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby('GAMEID', sort=False)}
    names = ['team_credit_prior_miss_exact','team_credit_endpoint_clock_consensus','team_credit_prior_miss_endpoint_clock_agree']
    ctl = {n: {'applicable': 0, 'correct': 0, 'wrong': 0} for n in names}
    wrong = {n: [] for n in names}
    candidates = []
    for gid in ids:
        lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
        ev = lu.events
        joined, _ = rebound.join_pbp_rebounds(lu, pg[gid])
        rows = a.rows_for_game(pg[gid])
        for idx, row in rows.iterrows():
            matched = idx in joined.index and pd.notna(joined.loc[idx, 'NBA_INDEX'])
            exclude = int(joined.loc[idx, 'NBA_INDEX']) if matched else None
            rr = rules(ev, row, exclude)
            if matched:
                ni = int(joined.loc[idx, 'NBA_INDEX'])
                actual = tuple(int(x) for x in ev.loc[ni, 'LINEUP'])
                real = bool(core._nba_real_rebound(ev, ni))
                for n, pred in rr.items():
                    ctl[n]['applicable'] += 1
                    good = (tuple(pred) == actual and real)
                    ctl[n]['correct' if good else 'wrong'] += 1
                    if not good:
                        wrong[n].append({'game_id': gid, 'pbp_index': int(idx), 'description': str(row.DESCRIPTION), 'actual_real': real, 'actual': list(actual), 'predicted': list(pred)})
            elif idx not in joined.index and rr:
                candidates.append({
                    'game_id': gid,
                    'pbp_index': int(idx),
                    'period': int(row.PERIOD),
                    'start_time': str(row.STARTTIME),
                    'end_time': str(row.ENDTIME),
                    'description': str(row.DESCRIPTION),
                    'pbp_is_oreb': bool(row.PBP_IS_OREB),
                    'strategies': {k: list(v) for k, v in rr.items()}
                })
    z.output.write_text(json.dumps({'status': 'DIAGNOSTIC_ONLY', 'chunk_id': z.chunk_id, 'year': z.year, 'controls': ctl, 'wrong_records': wrong, 'residual_candidates': candidates}, indent=2) + '\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
