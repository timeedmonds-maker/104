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

# Exact source-proven period-opening clock gap.
# In game 20400152 the corrected PBP Stats event stream places the period-4
# start marker (EVENTNUM 375, EVENTMSGTYPE 12) at 11:39 rather than 12:00.
# The legacy NBA event reconstruction otherwise allocates those unrecorded 21
# seconds to the inferred quarter-opening ten. Retained-core season seconds and
# the corrected historical event stream independently isolate the same gap.
# Keep this repair keyed to this game/period only; do not generalize it.
PERIOD_START_GAP_REPAIRS = {
    (20400152, 4): {
        "start_event": 375,
        "start_clock": "11:39",
        "seconds_removed": 21,
    }
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

    # IMPORTANT: do not generically delete rows whose EVENTNUM follows a period-end
    # marker. Legacy NBA Stats feeds contain non-chronological EVENTNUM values and
    # replay/correction inserts; any genuine anomaly requires an explicit key.
    return game, repairs


def reconstruct_game_lineups(game: pd.DataFrame) -> core.GameLineups:
    prepared, repairs = prepare_nba_game(game)
    result = core.reconstruct_game_lineups(prepared)
    result.repairs.extend(repairs)

    game_id = int(prepared.GAME_ID.iloc[0]) if not prepared.empty else 0
    for (repair_game, period_number), spec in PERIOD_START_GAP_REPAIRS.items():
        if game_id != repair_game:
            continue
        source = prepared[(prepared.PERIOD.eq(period_number)) & prepared.EVENTNUM.eq(int(spec["start_event"]))]
        if len(source) != 1:
            raise ValueError(f"locked period-start repair source event missing game={game_id} period={period_number}")
        row = source.iloc[0]
        if int(row.EVENTMSGTYPE) != 12 or str(row.PCTIMESTRING) != str(spec["start_clock"]):
            raise ValueError(
                f"locked period-start repair source changed game={game_id} period={period_number}: "
                f"type={int(row.EVENTMSGTYPE)} clock={row.PCTIMESTRING}"
            )
        gap = int(spec["seconds_removed"])
        expected_gap = core.elapsed_seconds(period_number, spec["start_clock"]) - (
            (period_number - 1) * 720 if period_number <= 4 else 2880 + (period_number - 5) * 300
        )
        if expected_gap != gap:
            raise ValueError(f"locked period-start repair gap mismatch: {expected_gap} != {gap}")

        period_events = result.events[result.events.PERIOD.eq(period_number)]
        first_elapsed = int(period_events.ELAPSED.min())
        first = period_events[period_events.ELAPSED.eq(first_elapsed)].sort_values("EVENTNUM", kind="stable").iloc[0]
        same_time_subs = period_events[(period_events.ELAPSED.eq(first_elapsed)) & period_events.EVENTMSGTYPE.eq(8)]
        if len(same_time_subs):
            raise ValueError(f"locked period-start repair opening lineup ambiguous game={game_id} period={period_number}")
        players = [int(p) for p in first.LINEUP]
        if len(players) != 10:
            raise ValueError(f"locked period-start repair expected ten players, got {len(players)}")
        for player in players:
            if result.seconds.get(player, 0) < gap:
                raise ValueError(f"locked period-start repair would make player {player} seconds negative")
            result.seconds[player] -= gap
        result.repairs.append({
            "game_id": game_id,
            "period": period_number,
            "type": "period_start_clock_gap_repair",
            "source_event": int(spec["start_event"]),
            "source_clock": str(spec["start_clock"]),
            "seconds_removed": gap,
            "players": sorted(players),
            "evidence": "corrected PBP Stats stream starts period at 11:39; nominal 12:00-to-11:39 interval is not recorded play",
        })
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
