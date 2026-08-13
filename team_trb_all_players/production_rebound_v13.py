#!/usr/bin/env python3
"""Production rebound layer V13 under test: V12 plus one exact Agbaji real-NBA rebound identity."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import local_treb_rebuild as core
import production_rebound_v12 as base

_EVIDENCE = Path(__file__).resolve().parent / 'final_integrity_rebuild' / 'rebound_forensics' / 'V13_NEARBY_PLAYER_REBOUND_REPAIR.json'


def _load():
    d = json.loads(_EVIDENCE.read_text())
    assert d['status'] == 'CANDIDATE_FOR_PROMOTION' and d['repair_rows'] == 1
    ctl = d['safe_controls']['unique_nearest_real_nba_player_rebound_exact_name_within_3s']
    assert ctl == {'applicable': 4888, 'exact_nba_event_identity_correct': 4888, 'exact_lineup_correct': 4888, 'wrong': 0}
    by_game = {}
    keys = set()
    for r in d['repairs']:
        assert r['resolution_method'] == 'unique_nearest_real_nba_player_rebound_exact_name_within_3s_and_unassigned'
        assert r['real'] is True
        key = (int(r['game_id']), int(r['pbp_index']))
        keys.add(key)
        by_game.setdefault(int(r['game_id']), []).append(r)
    assert keys == {(22201076, 494)}
    return by_game


def join_pbp_rebounds(lineups, pbp_game, alpha: int = 5):
    joined, audit = base.join_pbp_rebounds(lineups, pbp_game, alpha=alpha)
    gid = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    recs = _load().get(gid, [])
    if not recs:
        audit = dict(audit)
        audit['v13_exact_player_event_repairs'] = 0
        return joined, audit

    nba = lineups.events
    rebounds = pbp_game[pbp_game.DESCRIPTION.fillna('').str.contains('rebound', case=False)].copy()
    used = set(pd.to_numeric(joined.NBA_INDEX, errors='coerce').dropna().astype(int)) if 'NBA_INDEX' in joined.columns else set()
    additions = []
    applied = []

    for rec in recs:
        hit = rebounds[
            rebounds.PERIOD.eq(int(rec['period']))
            & rebounds.STARTTIME.astype(str).eq(str(rec['start_time']))
            & rebounds.ENDTIME.astype(str).eq(str(rec['end_time']))
            & rebounds.DESCRIPTION.astype(str).eq(str(rec['pbp_description']))
        ]
        if len(hit) != 1:
            raise ValueError(f"V13 PBP identity mismatch game={gid} hits={len(hit)} target={rec['pbp_index']}")
        pi = int(hit.index[0])
        if pi != int(rec['pbp_index']):
            raise ValueError(f"V13 PBP index drift game={gid} actual={pi} target={rec['pbp_index']}")
        if pi in joined.index:
            raise ValueError(f"V13 PBP row unexpectedly already joined game={gid} pbp_index={pi}")

        nh = nba[nba.PERIOD.eq(int(rec['period'])) & nba.EVENTNUM.eq(int(rec['nba_eventnum']))]
        if len(nh) != 1:
            raise ValueError(f"V13 NBA identity mismatch game={gid} eventnum={rec['nba_eventnum']} hits={len(nh)}")
        ni = int(nh.index[0])
        if ni in used:
            raise ValueError(f"V13 NBA event reuse game={gid} nba_index={ni}")
        if int(nba.loc[ni, 'EVENTMSGTYPE']) != 4:
            raise ValueError('V13 target NBA event is not a rebound')
        if int(nba.loc[ni, 'PLAYER1_ID']) != int(rec['nba_player1_id']):
            raise ValueError('V13 rebounder identity drift')
        if int(nba.loc[ni, 'ELAPSED']) != int(rec['nba_elapsed']):
            raise ValueError('V13 elapsed drift')
        if str(nba.loc[ni, 'DESCRIPTION_NORM']) != str(rec['nba_description_norm']):
            raise ValueError('V13 normalized NBA description drift')
        real = bool(core._nba_real_rebound(nba, ni))
        if real is not True or real != bool(rec['real']):
            raise ValueError('V13 real/dead rebound drift')
        lineup = [int(x) for x in nba.loc[ni, 'LINEUP']]
        if lineup != [int(x) for x in rec['lineup']]:
            raise ValueError('V13 lineup drift')

        start_elapsed = core.elapsed_seconds(int(rec['period']), str(rec['start_time']))
        end_elapsed = core.elapsed_seconds(int(rec['period']), str(rec['end_time']))
        lo, hi = min(start_elapsed, end_elapsed), max(start_elapsed, end_elapsed)
        distance = 0 if lo <= int(rec['nba_elapsed']) <= hi else min(abs(int(rec['nba_elapsed']) - lo), abs(int(rec['nba_elapsed']) - hi))
        if distance > 3:
            raise ValueError(f"V13 proximity drift game={gid} distance={distance}")

        add = hit.copy()
        add['NBA_INDEX'] = ni
        add['LINEUP'] = pd.Series([nba.loc[ni, 'LINEUP']], index=add.index, dtype=object)
        for col in ('EVENTMSGTYPE', 'EVENTMSGACTIONTYPE', 'PLAYER1_ID', 'ELAPSED', 'EVENTNUM'):
            add['NBA_' + col] = nba.loc[ni, col]
        add['NBA_IS_REAL_REBOUND'] = True
        add['REBOUND_LINEAGE'] = 'v13_exact_nearby_player_event_identity_unassigned'
        additions.append(add)
        used.add(ni)
        applied.append({'pbp_index': pi, 'nba_index': ni, 'nba_eventnum': int(rec['nba_eventnum']), 'nba_player1_id': int(rec['nba_player1_id'])})

    joined = pd.concat([joined] + additions, axis=0).sort_index(kind='stable')
    audit = dict(audit)
    n = len(additions)
    audit['matched_rebound_bearing_rows'] = int(len(joined))
    audit['unmatched_rebound_bearing_rows'] = max(0, int(audit.get('unmatched_rebound_bearing_rows', n)) - n)
    audit['v13_exact_player_event_repairs'] = n
    audit['v13_exact_player_event_records'] = applied
    return joined, audit


def classify_rebounds(pbp_game):
    return base.classify_rebounds(pbp_game)
