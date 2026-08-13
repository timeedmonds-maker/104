#!/usr/bin/env python3
"""Production rebound layer V9: V8 plus one zero-error non-bracket DREB endpoint/clock repair."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import production_rebound_v8 as base

_EVIDENCE = Path(__file__).resolve().parent / 'final_integrity_rebuild' / 'rebound_forensics' / 'V9_DREB_ENDPOINT_REPAIR.json'


def _load():
    d = json.loads(_EVIDENCE.read_text())
    assert d['status'] == 'PROMOTED' and d['repair_rows'] == 1
    assert d['safe_controls']['nonbracket_dreb_counter_endpoint_named'] == {'applicable': 279, 'correct': 279, 'wrong': 0}
    assert d['safe_controls']['nonbracket_dreb_counter_clock_named'] == {'applicable': 296, 'correct': 296, 'wrong': 0}
    assert d['safe_controls']['nonbracket_dreb_counter_endpoint_clock_named'] == {'applicable': 279, 'correct': 279, 'wrong': 0}
    by_game = {}
    keys = set()
    for r in d['repairs']:
        assert r['resolution_method'] == 'nonbracket_dreb_counter_endpoint_clock_named'
        assert r['real'] is True and r['pbp_is_oreb'] is False
        key = (int(r['game_id']), int(r['pbp_index']))
        keys.add(key)
        by_game.setdefault(int(r['game_id']), []).append(r)
    assert keys == {(21700032, 5033)}
    return by_game


def join_pbp_rebounds(lineups, pbp_game, alpha: int = 5):
    joined, audit = base.join_pbp_rebounds(lineups, pbp_game, alpha=alpha)
    gid = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    recs = _load().get(gid, [])
    if not recs:
        audit = dict(audit)
        audit['v9_dreb_endpoint_repairs'] = 0
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
            raise ValueError(f"V9 PBP identity mismatch game={gid} hits={len(hit)} target={rec['pbp_index']}")
        pi = int(hit.index[0])
        if pi in joined.index:
            raise ValueError(f"V9 row unexpectedly already joined game={gid} pbp_index={pi}")
        lu = tuple(int(x) for x in rec['lineup'])
        assert len(lu) == 10 and len(set(lu)) == 10 and int(rec['resolved_player_id']) in lu
        add = hit.copy()
        add['NBA_INDEX'] = pd.NA
        add['LINEUP'] = pd.Series([lu], index=add.index, dtype=object)
        for col in ('EVENTMSGTYPE', 'EVENTMSGACTIONTYPE', 'PLAYER1_ID', 'ELAPSED', 'EVENTNUM'):
            add['NBA_' + col] = pd.NA
        add['NBA_IS_REAL_REBOUND'] = True
        add['REBOUND_LINEAGE'] = 'v9_dreb_endpoint_synthesis:' + str(rec['resolution_method'])
        additions.append(add)
    joined = pd.concat([joined] + additions, axis=0).sort_index(kind='stable')
    audit = dict(audit)
    n = len(additions)
    audit['matched_rebound_bearing_rows'] = int(len(joined))
    audit['unmatched_rebound_bearing_rows'] = max(0, int(audit.get('unmatched_rebound_bearing_rows', n)) - n)
    audit['v9_dreb_endpoint_repairs'] = n
    return joined, audit


def classify_rebounds(pbp_game):
    return base.classify_rebounds(pbp_game)
