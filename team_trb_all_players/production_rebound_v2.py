#!/usr/bin/env python3
"""Evidence-backed production rebound layer.

Two deliberately narrow changes over production_treb_engine:
1) after the baseline fuzzy join fails, allow an unused NBA EVENTMSGTYPE=4 row
   only when its normalized description is an exact match (or exact player +
   cumulative rebound counter identity); and
2) classify the first N *real* rebounds in a possession as the authoritative N
   offensive rebounds, so non-live placeholder rows cannot consume an OREB slot.
"""
from __future__ import annotations

import re
import pandas as pd

import local_treb_rebuild as core
import production_treb_engine as legacy


def _norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def join_pbp_rebounds(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha: int = 5) -> tuple[pd.DataFrame, dict]:
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
    for _, row in rows:
        candidates = nba[(nba.PERIOD.eq(row.PERIOD)) &
                         (nba.ELAPSED.gt(row.START_ELAPSED - alpha)) &
                         (nba.ELAPSED.lt(row.END_ELAPSED + alpha))]
        scored = [(core._distance(row.DESCRIPTION_NORM, desc), int(ev), int(pos))
                  for pos, (ev, desc) in zip(candidates.index, zip(candidates.EVENTNUM, candidates.DESCRIPTION_NORM))]
        acceptable = [item for item in scored if item[0] < .2]
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

    used = {int(x) for x in matches if x is not None}
    counter_re = re.compile(r"\(off:(\d+) def:(\d+)\)", re.I)

    def name_key(value: object) -> str:
        return _norm(value).split(" rebound", 1)[0].strip()

    exact_identity = 0
    exact_description = 0
    exact_player_counter = 0
    records: list[dict] = []
    for position, (_, row) in enumerate(rows):
        if matches[position] is not None:
            continue
        eligible = nba[(nba.PERIOD.eq(row.PERIOD)) & nba.EVENTMSGTYPE.eq(4) & ~nba.index.isin(used)]
        chosen: int | None = None
        method: str | None = None
        exact = eligible[eligible.DESCRIPTION_NORM.eq(row.DESCRIPTION_NORM)]
        if len(exact) == 1:
            chosen = int(exact.index[0])
            method = "exact_description"
        else:
            counter = counter_re.search(row.DESCRIPTION_NORM)
            if counter:
                counter_key = f"(off:{counter.group(1)} def:{counter.group(2)})"
                player_key = name_key(row.DESCRIPTION_NORM)
                hits = eligible[
                    eligible.DESCRIPTION_NORM.str.contains(re.escape(counter_key), regex=True) &
                    eligible.DESCRIPTION_NORM.map(name_key).eq(player_key)
                ]
                if len(hits) == 1:
                    chosen = int(hits.index[0])
                    method = "exact_player_counter"
        if chosen is not None:
            matches[position] = chosen
            used.add(chosen)
            exact_identity += 1
            exact_description += int(method == "exact_description")
            exact_player_counter += int(method == "exact_player_counter")
            records.append({
                "period": int(row.PERIOD),
                "pbp_description": str(row.DESCRIPTION),
                "nba_eventnum": int(nba.loc[chosen, "EVENTNUM"]),
                "nba_description_norm": str(nba.loc[chosen, "DESCRIPTION_NORM"]),
                "method": method,
            })

    unmatched_rows = []
    for position, (_, row) in enumerate(rows):
        if matches[position] is None:
            unmatched_rows.append({
                "game_id": game_id,
                "period": int(row.PERIOD),
                "start_time": str(row.STARTTIME),
                "end_time": str(row.ENDTIME),
                "description": str(row.DESCRIPTION),
            })

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
        "exact_description_repairs": int(exact_description),
        "exact_player_counter_repairs": int(exact_player_counter),
        "exact_identity_records": records,
        "unmatched_rows": unmatched_rows,
    }
    return matched, audit


def classify_rebounds(pbp_game: pd.DataFrame) -> pd.DataFrame:
    """Use authoritative possession OREB count over real rebounds only."""
    out = core.classify_rebounds(pbp_game)
    real = out.IS_REAL_REBOUND.astype(bool)
    real_number = real.astype(int).groupby([out[c] for c in core.POSSESSION_ID], dropna=False).cumsum()
    possession_oreb = out.groupby(core.POSSESSION_ID, dropna=False).OFFENSIVEREBOUNDS.transform("first")
    out["REAL_REBOUND_NUMBER"] = real_number
    out["IS_OREB"] = real & real_number.le(possession_oreb)
    return out
