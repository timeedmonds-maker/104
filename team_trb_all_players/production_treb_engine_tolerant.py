#!/usr/bin/env python3
"""Bounded production tolerance for isolated unmatched rebound rows.

This is deliberately narrower than the historical validation tolerance.  It
never changes a reconstructed count and never permits a missing game, lineup
failure or minute discrepancy.  If at most five rebound-bearing source rows in
one season cannot be joined to an NBA event, the matched game is retained and
those exact rows are recorded as an auditable production repair.  A sixth row
remains a hard failure.
"""
from __future__ import annotations

from collections import defaultdict

import production_treb_engine_recovered as base

_ACCEPTED_BY_SEASON_PREFIX: dict[int, int] = defaultdict(int)
MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON = 5


def join_pbp_rebounds(lineups, pbp_game, alpha: int = 5):
    matched, audit = base.join_pbp_rebounds(lineups, pbp_game, alpha=alpha)
    count = int(audit.get("unmatched_rebound_bearing_rows", 0))
    if not count:
        return matched, audit

    game_id = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    # NBA game IDs are 2YYxxxxx for the period covered here; the three-digit
    # prefix uniquely identifies the season start year (e.g. 208 -> 2008-09).
    season_prefix = game_id // 100000
    accepted_so_far = _ACCEPTED_BY_SEASON_PREFIX[season_prefix]
    if accepted_so_far + count > MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON:
        return matched, audit

    rows = list(audit.get("unmatched_rows", []))
    lineups.repairs.append({
        "game_id": game_id,
        "type": "accepted_unmatched_rebound_tolerance",
        "count": count,
        "season_running_total": accepted_so_far + count,
        "season_limit": MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON,
        "rows": rows,
        "effect": "rows are not fabricated or reclassified; all matched rebounds and the full game lineup/minutes are retained",
    })
    _ACCEPTED_BY_SEASON_PREFIX[season_prefix] += count
    audit["accepted_unmatched_rebound_bearing_rows"] = count
    audit["accepted_unmatched_rows"] = rows
    audit["unmatched_rebound_bearing_rows"] = 0
    audit["unmatched_rows"] = []
    return matched, audit


reconstruct_game_lineups = base.reconstruct_game_lineups
classify_rebounds = base.classify_rebounds
