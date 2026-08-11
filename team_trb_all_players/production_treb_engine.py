#!/usr/bin/env python3
"""Production extensions for the validated historical TREB reconstruction engine.

The locked 2016 regression engine in ``local_treb_rebuild`` remains untouched.
This module adds only explicit, audited legacy-feed repairs discovered during
cross-era validation / the first two production seasons.
"""
from __future__ import annotations

import re

import pandas as pd

import local_treb_rebuild as core

PRODUCTION_STARTER_REPAIRS = {
    (20000202, 3, 1610612739): [221, 441, 980, 1723, 1889],
    (20000803, 3, 1610612739): [221, 754, 1538, 1889, 2036],
    (20100514, 4, 1610612747): [109, 283, 955, 977, 1904],
    (20101185, 4, 1610612742): [89, 271, 1761, 1915, 1917],
    (20400826, 1, 1610612738): [952, 1711, 1718, 1729, 2753],
    (20800135, 4, 1610612739): [980, 2544, 2753, 2760, 200789],
}

SUBSTITUTION_OUT_REPAIRS = {
    (20000883, 458): None,
    (20101009, 430): 722,
    (20101009, 436): 1802,
}

JOIN_REPAIRS = {
    (20000758, 4, "gatling rebound (off:2 def:3)"): 394,
    (20000998, 1, "wizards rebound"): 114,
    (22400208, 1, "murray rebound (off:0 def:1)"): 170,
    (22401102, 5, "mcdaniels rebound (off:4 def:1)"): 709,
}

core.STARTER_REPAIRS.update(PRODUCTION_STARTER_REPAIRS)


def _norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def prepare_nba_game(game: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Apply only explicit feed repairs before core lineup reconstruction."""
    game = game.copy()
    repairs: list[dict] = []
    if game.empty:
        return game, repairs
    game_id = int(game.GAME_ID.iloc[0])

    drop_index = []
    for idx, row in game.loc[game.EVENTMSGTYPE.eq(8)].iterrows():
        key = (game_id, int(row.EVENTNUM))
        if key not in SUBSTITUTION_OUT_REPAIRS:
            continue
        corrected = SUBSTITUTION_OUT_REPAIRS[key]
        if corrected is None:
            drop_index.append(idx)
            repairs.append({"game_id": game_id, "event_num": int(row.EVENTNUM),
                            "type": "drop_zero_id_substitution",
                            "evidence": "legacy NBA Stats feed contains a substitution row with no players"})
        else:
            old = int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
            game.at[idx, "PLAYER1_ID"] = int(corrected)
            repairs.append({"game_id": game_id, "event_num": int(row.EVENTNUM),
                            "type": "substitution_outgoing_repair",
                            "original_outgoing": old, "repaired_outgoing": int(corrected),
                            "incoming": int(row.PLAYER2_ID) if pd.notna(row.PLAYER2_ID) else 0,
                            "evidence": "duplicated/stale legacy substitution sequence"})
    if drop_index:
        game = game.drop(index=drop_index)

    bad_after_horn = []
    for period_no, period in game.groupby("PERIOD", sort=False):
        ordered = period.sort_values("EVENTNUM", kind="stable")
        horns = ordered.loc[ordered.EVENTMSGTYPE.eq(13), "EVENTNUM"]
        if horns.empty:
            continue
        first_horn = int(horns.iloc[0])
        later = ordered[ordered.EVENTNUM.gt(first_horn)]
        for idx, row in later.iterrows():
            if str(row.PCTIMESTRING) not in {"0:00", "00:00", "0:00.0", "00:00.0"} and int(row.EVENTMSGTYPE) != 18:
                bad_after_horn.append(idx)
                repairs.append({"game_id": game_id, "period": int(period_no),
                                "event_num": int(row.EVENTNUM), "type": "post_horn_clock_repair",
                                "clock": str(row.PCTIMESTRING),
                                "evidence": "non-zero-clock row appears after explicit period-ending horn"})
    if bad_after_horn:
        game = game.drop(index=bad_after_horn)
    return game, repairs


def reconstruct_game_lineups(game: pd.DataFrame) -> core.GameLineups:
    prepared, repairs = prepare_nba_game(game)
    result = core.reconstruct_game_lineups(prepared)
    result.repairs.extend(repairs)
    return result


def join_pbp_rebounds(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha: int = 5) -> tuple[pd.DataFrame, dict]:
    """Core PBP/NBA join with explicit one-row historical join repairs."""
    ordered_pbp = pbp_game.copy()
    ordered_pbp["PREV_PBP_DESCRIPTION"] = ordered_pbp.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    rebounds = ordered_pbp[ordered_pbp.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    rebounds["DESCRIPTION_NORM"] = rebounds.DESCRIPTION.map(_norm)
    rebounds["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.STARTTIME)]
    rebounds["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.ENDTIME)]
    nba = lineups.events
    game_id = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0

    matches: list[int | None] = []
    ambiguous = 0
    manual = 0
    unmatched_rows: list[dict] = []
    for _, row in rebounds.iterrows():
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
        repair_event = JOIN_REPAIRS.get((game_id, int(row.PERIOD), row.DESCRIPTION_NORM))
        if repair_event is not None:
            hit = nba[(nba.PERIOD.eq(row.PERIOD)) & nba.EVENTNUM.eq(repair_event)]
            if len(hit) == 1:
                matches.append(int(hit.index[0]))
                manual += 1
                continue
        matches.append(None)
        unmatched_rows.append({"game_id": game_id, "period": int(row.PERIOD),
                               "start_time": str(row.STARTTIME), "end_time": str(row.ENDTIME),
                               "description": str(row.DESCRIPTION)})

    rebounds["NBA_INDEX"] = matches
    matched = rebounds[rebounds.NBA_INDEX.notna()].copy()
    matched["LINEUP"] = [nba.loc[int(i), "LINEUP"] for i in matched.NBA_INDEX]
    for column in ("EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PLAYER1_ID", "ELAPSED", "EVENTNUM"):
        matched["NBA_" + column] = [nba.loc[int(i), column] for i in matched.NBA_INDEX]
    matched["NBA_IS_REAL_REBOUND"] = [core._nba_real_rebound(nba, int(i)) for i in matched.NBA_INDEX]
    audit = {"total_pbp_rows": int(len(pbp_game)), "rebound_bearing_rows": int(len(rebounds)),
             "matched_rebound_bearing_rows": int(len(matched)),
             "unmatched_rebound_bearing_rows": int(len(unmatched_rows)),
             "ambiguous_matches": int(ambiguous), "manual_join_repairs": int(manual),
             "unmatched_rows": unmatched_rows}
    return matched, audit


classify_rebounds = core.classify_rebounds
