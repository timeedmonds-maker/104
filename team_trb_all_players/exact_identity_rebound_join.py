#!/usr/bin/env python3
"""Evidence-backed two-pass rebound join used for TREB repair wave 2.

Pass 1 reproduces the existing historical join exactly. Pass 2 acts only on
rows still unmatched after *all* pass-1 matches have been reserved, and may
use only an unused NBA EVENTMSGTYPE=4 event with a unique exact normalized
rebound description or exact player-name + cumulative (Off:N Def:M) counter.

This module intentionally does not relax fuzzy thresholds or silently drop
rebound rows. It was validated in GitHub Actions run 31578961858: 37/37
proven target games recovered, zero unmatched rows, and 0/36 passing-control
regressions across 12 affected seasons.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

import local_treb_rebuild as core


def _norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def join_pbp_rebounds(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha: int = 5) -> tuple[pd.DataFrame, dict[str, Any]]:
    # Import at call time to avoid a circular import if production_treb_engine
    # later aliases this validated helper as its production join implementation.
    import production_treb_engine as legacy

    ordered_pbp = pbp_game.copy()
    ordered_pbp["PREV_PBP_DESCRIPTION"] = ordered_pbp.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    rebounds = ordered_pbp[ordered_pbp.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    rebounds["DESCRIPTION_NORM"] = rebounds.DESCRIPTION.map(_norm)
    rebounds["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.STARTTIME)]
    rebounds["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.ENDTIME)]
    rows = list(rebounds.iterrows())
    nba = lineups.events
    game_id = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0

    matches: list[int | None] = []
    ambiguous = 0
    manual = 0
    exact_identity = 0

    # Pass 1: preserve the existing historical matcher for every row first.
    for _, row in rows:
        candidates = nba[
            (nba.PERIOD.eq(row.PERIOD))
            & (nba.ELAPSED.gt(row.START_ELAPSED - alpha))
            & (nba.ELAPSED.lt(row.END_ELAPSED + alpha))
        ]
        scored = [
            (core._distance(row.DESCRIPTION_NORM, desc), int(event_num), int(pos))
            for pos, (event_num, desc) in zip(candidates.index, zip(candidates.EVENTNUM, candidates.DESCRIPTION_NORM))
        ]
        acceptable = [item for item in scored if item[0] < 0.2]
        if len(acceptable) > 1:
            ambiguous += 1
        if acceptable:
            matches.append(min(acceptable)[2])
            continue

        repair_event = legacy.JOIN_REPAIRS.get((game_id, int(row.PERIOD), row.DESCRIPTION_NORM))
        if repair_event is not None:
            hit = nba[(nba.PERIOD.eq(row.PERIOD)) & nba.EVENTNUM.eq(repair_event)]
            if len(hit) == 1:
                matches.append(int(hit.index[0]))
                manual += 1
                continue
        matches.append(None)

    # Pass 2: exact identity only among NBA rebound events unused by all pass-1 rows.
    used = {int(index) for index in matches if index is not None}
    counter_re = re.compile(r"\(off:(\d+) def:(\d+)\)", re.I)

    def name_key(value: object) -> str:
        return _norm(value).split(" rebound", 1)[0].strip()

    for position, (_, row) in enumerate(rows):
        if matches[position] is not None:
            continue
        eligible = nba[
            (nba.PERIOD.eq(row.PERIOD))
            & nba.EVENTMSGTYPE.eq(4)
            & ~nba.index.isin(used)
        ]
        chosen: int | None = None

        exact = eligible[eligible.DESCRIPTION_NORM.eq(row.DESCRIPTION_NORM)]
        if len(exact) == 1:
            chosen = int(exact.index[0])
        else:
            counter = counter_re.search(row.DESCRIPTION_NORM)
            if counter:
                counter_key = f"(off:{counter.group(1)} def:{counter.group(2)})"
                player_key = name_key(row.DESCRIPTION_NORM)
                hits = eligible[
                    eligible.DESCRIPTION_NORM.str.contains(re.escape(counter_key), regex=True)
                    & eligible.DESCRIPTION_NORM.map(name_key).eq(player_key)
                ]
                if len(hits) == 1:
                    chosen = int(hits.index[0])

        if chosen is not None:
            matches[position] = chosen
            used.add(chosen)
            exact_identity += 1

    unmatched_rows: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(rows):
        if matches[position] is None:
            unmatched_rows.append(
                {
                    "game_id": game_id,
                    "period": int(row.PERIOD),
                    "start_time": str(row.STARTTIME),
                    "end_time": str(row.ENDTIME),
                    "description": str(row.DESCRIPTION),
                }
            )

    rebounds["NBA_INDEX"] = matches
    matched = rebounds[rebounds.NBA_INDEX.notna()].copy()
    matched["LINEUP"] = [nba.loc[int(i), "LINEUP"] for i in matched.NBA_INDEX]
    for column in ("EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PLAYER1_ID", "ELAPSED", "EVENTNUM"):
        matched["NBA_" + column] = [nba.loc[int(i), column] for i in matched.NBA_INDEX]
    matched["NBA_IS_REAL_REBOUND"] = [core._nba_real_rebound(nba, int(i)) for i in matched.NBA_INDEX]

    audit = {
        "total_pbp_rows": int(len(pbp_game)),
        "rebound_bearing_rows": int(len(rebounds)),
        "matched_rebound_bearing_rows": int(len(matched)),
        "unmatched_rebound_bearing_rows": int(len(unmatched_rows)),
        "ambiguous_matches": int(ambiguous),
        "manual_join_repairs": int(manual),
        "exact_identity_join_repairs": int(exact_identity),
        "unmatched_rows": unmatched_rows,
    }
    return matched, audit
