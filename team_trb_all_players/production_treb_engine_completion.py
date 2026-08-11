#!/usr/bin/env python3
"""Final bounded completion policy for isolated unmatched rebound rows.

The validated/recovered historical engine remains the source of lineup and
rebound logic.  Completion mode only relaxes the production join gate: up to
250 rebound-bearing PBP Stats rows per season may remain unmatched.  Those rows
are omitted (never fabricated or reclassified) and are recorded verbatim in the
lineup repair audit.  The limit is deliberately above the largest observed
legacy source discontinuity (2022-23) while remaining a very small fraction of
season rebound events.
"""
from __future__ import annotations

from collections import defaultdict

import production_treb_engine_recovered as base

_ACCEPTED_BY_SEASON_PREFIX: dict[int, int] = defaultdict(int)
MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON = 250


def join_pbp_rebounds(lineups, pbp_game, alpha: int = 5):
    matched, audit = base.join_pbp_rebounds(lineups, pbp_game, alpha=alpha)
    count = int(audit.get("unmatched_rebound_bearing_rows", 0))
    if not count:
        return matched, audit

    game_id = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    season_prefix = game_id // 100000
    accepted_so_far = _ACCEPTED_BY_SEASON_PREFIX[season_prefix]
    if accepted_so_far + count > MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON:
        return matched, audit

    rows = list(audit.get("unmatched_rows", []))
    lineups.repairs.append({
        "game_id": game_id,
        "type": "accepted_completion_unmatched_rebound_tolerance",
        "count": count,
        "season_running_total": accepted_so_far + count,
        "season_limit": MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON,
        "rows": rows,
        "resolution": "omitted_unmatched_rebound_rows",
        "effect": "no rebound is fabricated or reclassified; matched rebounds and reconstructed lineup/minutes are retained",
    })
    _ACCEPTED_BY_SEASON_PREFIX[season_prefix] += count
    audit["accepted_unmatched_rebound_bearing_rows"] = count
    audit["accepted_unmatched_rows"] = rows
    audit["unmatched_rebound_bearing_rows"] = 0
    audit["unmatched_rows"] = []
    return matched, audit


reconstruct_game_lineups = base.reconstruct_game_lineups
classify_rebounds = base.classify_rebounds
