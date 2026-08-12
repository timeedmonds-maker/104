#!/usr/bin/env python3
"""Stricter semantic candidate: prove one lineup across the full legal ±5s join window."""
from __future__ import annotations
import pandas as pd
import production_rebound_semantic_candidate as base


def _padded_invariant_lineup(nba: pd.DataFrame, row: pd.Series, alpha: int = 5):
    lo=min(int(row.START_ELAPSED),int(row.END_ELAPSED))-alpha
    hi=max(int(row.START_ELAPSED),int(row.END_ELAPSED))+alpha
    span=nba[nba.PERIOD.eq(row.PERIOD)&nba.ELAPSED.ge(lo)&nba.ELAPSED.le(hi)]
    if len(span)==0 or bool(span.EVENTMSGTYPE.eq(8).any()):
        return None
    lineups={tuple(int(x) for x in lu) for lu in span.LINEUP}
    if len(lineups)!=1:
        return None
    return next(iter(lineups))

# join_pbp_rebounds resolves this global from the base module at call time.
base._invariant_lineup=_padded_invariant_lineup
join_pbp_rebounds=base.join_pbp_rebounds
classify_rebounds=base.classify_rebounds
