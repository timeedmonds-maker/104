#!/usr/bin/env python3
"""Diagnostic-only enrichment of TREB rebound semantic mismatch rows with neighbouring NBA events.

This wrapper changes no reconstruction or classification semantics and emits no promotable primitives.
It monkey-patches only the evidence recorder in treb_rebound_semantic_audit so each mismatched
rebound row retains the immediately preceding and following NBA events. This is needed to distinguish
live rebound possession changes from dead-ball/out-of-bounds bookkeeping events using explicit source
chronology. Any subsequent classification rule must still validate at zero mismatch on exact controls.
"""
from __future__ import annotations
import json
import pandas as pd
import treb_rebound_semantic_audit as audit

_orig = audit.component_rows

def _safe_int(v):
    try:
        return int(v) if pd.notna(v) else 0
    except Exception:
        return 0

def _ctx(r, prefix):
    if r is None:
        return {
            f'{prefix}_eventnum': 0,
            f'{prefix}_elapsed': -1,
            f'{prefix}_description': '',
            f'{prefix}_player1_id': 0,
            f'{prefix}_player2_id': 0,
            f'{prefix}_player3_id': 0,
        }
    return {
        f'{prefix}_eventnum': _safe_int(r.get('EVENTNUM')),
        f'{prefix}_elapsed': _safe_int(r.get('ELAPSED')),
        f'{prefix}_description': str(r.get('DESCRIPTION_NORM','')),
        f'{prefix}_player1_id': _safe_int(r.get('PLAYER1_ID')),
        f'{prefix}_player2_id': _safe_int(r.get('PLAYER2_ID')),
        f'{prefix}_player3_id': _safe_int(r.get('PLAYER3_ID')),
    }

def enriched_component_rows(joined, nba, abbr, team_id, component, on=None):
    rows = _orig(joined, nba, abbr, team_id, component, on)
    ordered = nba.sort_values(['PERIOD','ELAPSED','EVENTNUM'], kind='stable')
    pos = {idx:i for i,idx in enumerate(ordered.index.tolist())}
    for rec in rows:
        ni = rec['nba_index']
        i = pos.get(ni)
        prev = ordered.iloc[i-1] if i is not None and i > 0 else None
        nxt = ordered.iloc[i+1] if i is not None and i + 1 < len(ordered) else None
        nxt2 = ordered.iloc[i+2] if i is not None and i + 2 < len(ordered) else None
        rec.update(_ctx(prev, 'prev_nba'))
        rec.update(_ctx(nxt, 'next_nba'))
        rec.update(_ctx(nxt2, 'next2_nba'))
        if nxt is not None:
            rec['next_same_clock'] = bool(_safe_int(nxt.get('ELAPSED')) == rec.get('nba_elapsed'))
            rec['next_elapsed_delta'] = _safe_int(nxt.get('ELAPSED')) - int(rec.get('nba_elapsed',0))
        else:
            rec['next_same_clock'] = False
            rec['next_elapsed_delta'] = -1
    return rows

audit.component_rows = enriched_component_rows

if __name__ == '__main__':
    raise SystemExit(audit.main())
