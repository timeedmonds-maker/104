#!/usr/bin/env python3
"""Production rebound layer V12: V9 plus one exact Sabonis DREB clock-invariant repair."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import production_rebound_v9 as base

_EVIDENCE = Path(__file__).resolve().parent / 'final_integrity_rebuild' / 'rebound_forensics' / 'V12_SABONIS_DREB_REPAIR.json'


def _load():
    d = json.loads(_EVIDENCE.read_text())
    assert d['status'] == 'PROMOTED' and d['repair_rows'] == 1
    assert d['safe_controls']['resolved_dreb_clock_invariant'] == {'applicable': 348, 'correct': 348, 'wrong': 0}
    assert d['safe_controls']['bracket_clock_named_dreb'] == {'applicable': 52, 'correct': 52, 'wrong': 0}
    assert d['safe_controls']['bracket_endpoint_clock_named_dreb'] == {'applicable': 51, 'correct': 51, 'wrong': 0}
    by_game = {}
    keys = set()
    for r in d['repairs']:
        assert r['resolution_method'] == 'resolved_dreb_clock_invariant'
        assert r['real'] is True and r['pbp_is_oreb'] is False
        key = (int(r['game_id']), int(r['pbp_index']))
        keys.add(key)
        by_game.setdefault(int(r['game_id']), []).append(r)
    assert keys == {(22201076, 529)}
    return by_game


def join_pbp_rebounds(lineups, pbp_game, alpha: int = 5):
    joined, audit = base.join_pbp_rebounds(lineups, pbp_game, alpha=alpha)
    gid = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    recs = _load().get(gid, [])
    if not recs:
        audit = dict(audit)
        audit['v12_sabonis_dreb_repairs'] = 0
        return joined, audit
    rebounds = pbp_game[pbp_game.DESCRIPTION.fillna('').str.contains('rebound', case=False)].copy()
    additions = []
    for rec in recs:
        hit = rebounds[
            rebounds.PERIOD.eq(int(rec['period']))
            & rebounds.STARTTIME.astype(str).eq(str(rec['start_time']))
            & rebounds.ENDTIME.astype(str).eq(str(rec['end_time']))
            & rebounds.DESCRIPTION.astype(str).eq(str(rec['pbp_description']))
        ]
        if len(hit) != 1:
            raise ValueError(f"V12 PBP identity mismatch game={gid} hits={len(hit)} target={rec['pbp_index']}")
        pi = int(hit.index[0])
        if pi != int(rec['pbp_index']):
            raise ValueError(f"V12 PBP index mismatch game={gid} actual={pi} target={rec['pbp_index']}")
        if pi in joined.index:
            raise ValueError(f"V12 row unexpectedly already joined game={gid} pbp_index={pi}")
        lu = tuple(int(x) for x in rec['lineup'])
        assert len(lu) == 10 and len(set(lu)) == 10 and int(rec['resolved_player_id']) in lu
        add = hit.copy()
        add['NBA_INDEX'] = pd.NA
        add['LINEUP'] = pd.Series([lu], index=add.index, dtype=object)
        for col in ('EVENTMSGTYPE', 'EVENTMSGACTIONTYPE', 'PLAYER1_ID', 'ELAPSED', 'EVENTNUM'):
            add['NBA_' + col] = pd.NA
        add['NBA_IS_REAL_REBOUND'] = True
        add['REBOUND_LINEAGE'] = 'v12_sabonis_dreb_synthesis:' + str(rec['resolution_method'])
        additions.append(add)
    joined = pd.concat([joined] + additions, axis=0).sort_index(kind='stable')
    audit = dict(audit)
    n = len(additions)
    audit['matched_rebound_bearing_rows'] = int(len(joined))
    audit['unmatched_rebound_bearing_rows'] = max(0, int(audit.get('unmatched_rebound_bearing_rows', n)) - n)
    audit['v12_sabonis_dreb_repairs'] = n
    return joined, audit


def classify_rebounds(pbp_game):
    return base.classify_rebounds(pbp_game)
