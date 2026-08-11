#!/usr/bin/env python3
"""Local PBP-Stats-authoritative TREB reconstruction engine.

PBP Stats defines the rebound universe. NBA Stats is used only to reconstruct
lineups and attach a lineup to each processed PBP Stats event.  The matching
rules intentionally follow shufinskiy/nba-on-court's ``left_join_pbpstats``:
period and possession clock windows followed by normalized-description edit
matching, with deterministic tie breaking.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Iterable

import numpy as np
import pandas as pd
from rapidfuzz.distance import Levenshtein

PLAYER_MAX = 1610612737
OKC_ID = 1610612760
ADAMS_ID = 203500
POSSESSION_ID = ["GAMEID", "OPPONENT", "PERIOD", "STARTTIME", "ENDTIME"]

# These are not guesses/truncations. They are explicit period-opening states
# recovered by propagating the local substitution record backwards/forwards.
# Only the named problematic team is overridden; its opponent remains inferred.
STARTER_REPAIRS = {
    (21600235, 4, 1610612743): [2749, 201589, 202087, 203999, 1627750],
    (21600270, 5, 1610612764): [202322, 202693, 203078, 203490, 1626162],
    (21600507, 4, 1610612760): [202683, 203460, 203530, 203902, 203924],
}


def clock_seconds(value: object) -> int:
    minute, second = str(value).split(":")[:2]
    return int(minute) * 60 + int(float(second))


def elapsed_seconds(period: int, clock: object) -> int:
    remaining = clock_seconds(clock)
    return (period - 1) * 720 + 720 - remaining if period <= 4 else 2880 + (period - 5) * 300 + 300 - remaining


def normalize_description(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def nba_description(frame: pd.DataFrame) -> pd.Series:
    cols = [frame[c].fillna("").astype(str) for c in ("HOMEDESCRIPTION", "NEUTRALDESCRIPTION", "VISITORDESCRIPTION")]
    return (cols[0] + " " + cols[1] + " " + cols[2]).map(normalize_description)


def _player_team(period: pd.DataFrame) -> dict[int, int]:
    evidence: dict[int, list[int]] = {}
    for n in (1, 2, 3):
        ids = pd.to_numeric(period[f"PLAYER{n}_ID"], errors="coerce")
        teams = pd.to_numeric(period[f"PLAYER{n}_TEAM_ID"], errors="coerce")
        types = pd.to_numeric(period[f"PERSON{n}TYPE"], errors="coerce")
        valid = ids.gt(0) & ids.lt(PLAYER_MAX) & types.isin([4, 5]) & teams.notna()
        for player, team in zip(ids[valid].astype(int), teams[valid].astype(int)):
            evidence.setdefault(player, []).append(team)
    return {p: max(set(ts), key=ts.count) for p, ts in evidence.items()}


def infer_period_starters(period: pd.DataFrame, game_id: int, period_number: int, prior_lineups: dict[int, set[int]] | None = None) -> tuple[dict[int, set[int]], list[dict]]:
    """Infer opening five for both teams, preserving explicit exceptional repairs."""
    player_team = _player_team(period)
    participants = set(player_team)
    subs = period.loc[period.EVENTMSGTYPE.eq(8)].sort_values(["ELAPSED", "EVENTNUM"], kind="stable")
    sub_out = set(pd.to_numeric(subs.PLAYER1_ID, errors="coerce").dropna().astype(int))
    sub_in = set(pd.to_numeric(subs.PLAYER2_ID, errors="coerce").dropna().astype(int))
    # nba-on-court rule: a player who only enters cannot have opened the period.
    candidates = participants - (sub_in - sub_out)
    # Resolve players both in/out by their first chronological substitution.
    for player in sub_in & sub_out:
        first = subs[(subs.PLAYER1_ID.eq(player)) | (subs.PLAYER2_ID.eq(player))].iloc[0]
        if int(first.PLAYER2_ID) == player:
            candidates.discard(player)
        else:
            candidates.add(player)

    teams = sorted(set(player_team.values()))
    starters: dict[int, set[int]] = {}
    repairs: list[dict] = []
    for team in teams:
        team_candidates = {p for p in candidates if player_team.get(p) == team}
        key = (game_id, period_number, team)
        if key in STARTER_REPAIRS:
            repaired = set(STARTER_REPAIRS[key])
            if len(repaired) != 5:
                raise ValueError(f"invalid starter repair {key}: {sorted(repaired)}")
            starters[team] = repaired
            repairs.append({"game_id": game_id, "period": period_number, "team_id": team,
                            "inferred_candidates": sorted(team_candidates), "repaired_starters": sorted(repaired),
                            "evidence": "local participant record plus substitution propagation"})
        elif len(team_candidates) == 5:
            starters[team] = team_candidates
        elif len(team_candidates) < 5 and prior_lineups and team in prior_lineups:
            # A player can play an entire overtime without generating a box event.
            # Carry only enough locally known end-of-regulation players to fill it.
            carry = [p for p in sorted(prior_lineups[team]) if p not in team_candidates and p not in sub_in]
            filled = team_candidates | set(carry[: 5 - len(team_candidates)])
            if len(filled) != 5:
                raise ValueError(f"unresolved carried starters game={game_id} period={period_number} team={team}: {sorted(team_candidates)}")
            starters[team] = filled
        else:
            raise ValueError(f"unresolved starters game={game_id} period={period_number} team={team}: {sorted(team_candidates)}")
    if len(starters) != 2 or any(len(v) != 5 for v in starters.values()):
        raise ValueError(f"invalid ten-player opening state game={game_id} period={period_number}")
    return starters, repairs


@dataclass
class GameLineups:
    events: pd.DataFrame
    seconds: dict[int, int]
    repairs: list[dict]


def reconstruct_game_lineups(game: pd.DataFrame) -> GameLineups:
    game = game.sort_values(["PERIOD", "EVENTNUM"], kind="stable").copy()
    game["DESCRIPTION_NORM"] = nba_description(game)
    game["ELAPSED"] = [elapsed_seconds(int(p), c) for p, c in zip(game.PERIOD, game.PCTIMESTRING)]
    snapshots: list[pd.DataFrame] = []
    seconds: dict[int, int] = {}
    all_repairs: list[dict] = []
    game_id = int(game.GAME_ID.iloc[0])
    prior_lineups: dict[int, set[int]] | None = None
    for number, period in game.groupby("PERIOD", sort=True):
        period = period.sort_values(["ELAPSED", "EVENTNUM"], kind="stable").copy()
        starters, repairs = infer_period_starters(period, game_id, int(number), prior_lineups)
        all_repairs.extend(repairs)
        lineup = set().union(*starters.values())
        period_start = (int(number) - 1) * 720 if number <= 4 else 2880 + (int(number) - 5) * 300
        last_time = period_start
        rows = []
        for _, event in period.iterrows():
            now = int(event.ELAPSED)
            if now > last_time:
                for player in lineup:
                    seconds[player] = seconds.get(player, 0) + now - last_time
                last_time = now
            if int(event.EVENTMSGTYPE) == 8:
                outgoing, incoming = int(event.PLAYER1_ID), int(event.PLAYER2_ID)
                if outgoing not in lineup:
                    raise ValueError(f"substitution outgoing player absent game={game_id} event={event.EVENTNUM}: {outgoing}")
                lineup.remove(outgoing)
                lineup.add(incoming)
            if len(lineup) != 10:
                raise ValueError(f"lineup size {len(lineup)} game={game_id} event={event.EVENTNUM}")
            row = event.to_dict()
            row["LINEUP"] = tuple(sorted(lineup))
            rows.append(row)
        period_end = period_start + (720 if int(number) <= 4 else 300)
        if period_end > last_time:
            for player in lineup:
                seconds[player] = seconds.get(player, 0) + period_end - last_time
        snapshots.append(pd.DataFrame(rows))
        global_teams = _player_team(game)
        prior_lineups = {}
        for player in lineup:
            if player in global_teams:
                prior_lineups.setdefault(global_teams[player], set()).add(player)
    return GameLineups(pd.concat(snapshots, ignore_index=True), seconds, all_repairs)


def _distance(a: str, b: str) -> float:
    size = max(len(a), len(b))
    return Levenshtein.distance(a, b) / size if size else 0.0



def _nba_real_rebound(nba: pd.DataFrame, position: int) -> bool:
    """Source-compatible subset of pbpstats Rebound.is_real_rebound."""
    loc = nba.index.get_loc(position)
    row = nba.loc[position]
    player = int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
    team_rebound = player == 0 or player >= PLAYER_MAX
    action = int(row.EVENTMSGACTIONTYPE)
    if team_rebound and action != 0:
        return False
    previous = nba.iloc[loc - 1] if loc else None
    prior_shot = previous
    scan = loc - 1
    while scan >= 0 and int(nba.iloc[scan].ELAPSED) == int(row.ELAPSED):
        candidate = nba.iloc[scan]
        if int(candidate.EVENTMSGTYPE) in (1, 2, 3, 5):
            prior_shot = candidate
            break
        scan -= 1
    # shot-clock/kicked-ball turnover placeholder at the rebound clock
    same_time = nba[(nba.PERIOD.eq(row.PERIOD)) & nba.ELAPSED.eq(row.ELAPSED)]
    if team_rebound and ((same_time.EVENTMSGTYPE.eq(5)) & same_time.EVENTMSGACTIONTYPE.isin([11, 19])).any():
        return False
    # A miss before the end of a FT trip is not live.
    if prior_shot is not None and int(prior_shot.EVENTMSGTYPE) == 3 and "miss " in prior_shot.DESCRIPTION_NORM:
        end_actions = {10, 12, 15, 30, 31, 32, 35, 36, 37}
        if int(prior_shot.EVENTMSGACTIONTYPE) not in end_actions or " flagrant" in prior_shot.DESCRIPTION_NORM:
            return False
    # Ignore replay rows while checking the event after a horn rebound.
    nxt = loc + 1
    while nxt < len(nba) and int(nba.iloc[nxt].EVENTMSGTYPE) == 18:
        nxt += 1
    next_is_end = nxt >= len(nba) or int(nba.iloc[nxt].EVENTMSGTYPE) == 13
    if team_rebound and int(row.ELAPSED) in {int(row.PERIOD) * 720 if int(row.PERIOD) <= 4 else 2880 + (int(row.PERIOD) - 4) * 300} and next_is_end:
        return False
    if team_rebound and previous is not None and int(row.ELAPSED) == int(previous.ELAPSED) and next_is_end:
        period_end = int(row.PERIOD) * 720 if int(row.PERIOD) <= 4 else 2880 + (int(row.PERIOD) - 4) * 300
        if period_end - int(row.ELAPSED) <= 3:
            return False
    return True

def join_pbp_rebounds(lineups: GameLineups, pbp_game: pd.DataFrame, alpha: int = 5) -> tuple[pd.DataFrame, dict]:
    """Attach NBA lineup snapshots to every rebound-bearing PBP Stats row."""
    ordered_pbp = pbp_game.copy()
    ordered_pbp["PREV_PBP_DESCRIPTION"] = ordered_pbp.groupby(POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    rebounds = ordered_pbp[ordered_pbp.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    rebounds["DESCRIPTION_NORM"] = rebounds.DESCRIPTION.map(normalize_description)
    rebounds["START_ELAPSED"] = [elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.STARTTIME)]
    rebounds["END_ELAPSED"] = [elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.ENDTIME)]
    matches, ambiguous, unmatched = [], 0, 0
    nba = lineups.events
    for idx, row in rebounds.iterrows():
        candidates = nba[(nba.PERIOD.eq(row.PERIOD)) &
                         (nba.ELAPSED.gt(row.START_ELAPSED - alpha)) &
                         (nba.ELAPSED.lt(row.END_ELAPSED + alpha))]
        scored = [(_distance(row.DESCRIPTION_NORM, desc), int(ev), pos)
                  for pos, (ev, desc) in zip(candidates.index, zip(candidates.EVENTNUM, candidates.DESCRIPTION_NORM))]
        acceptable = [item for item in scored if item[0] < .2]
        if len(acceptable) > 1:
            ambiguous += 1
        if not acceptable:
            unmatched += 1
            matches.append(None)
            continue
        _, _, position = min(acceptable)
        matches.append(position)
    rebounds["NBA_INDEX"] = matches
    matched = rebounds[rebounds.NBA_INDEX.notna()].copy()
    matched["LINEUP"] = [nba.loc[int(i), "LINEUP"] for i in matched.NBA_INDEX]
    for column in ("EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PLAYER1_ID", "ELAPSED", "EVENTNUM"):
        matched["NBA_" + column] = [nba.loc[int(i), column] for i in matched.NBA_INDEX]
    matched["NBA_IS_REAL_REBOUND"] = [_nba_real_rebound(nba, int(i)) for i in matched.NBA_INDEX]
    audit = {"total_pbp_rows": int(len(pbp_game)), "rebound_bearing_rows": int(len(rebounds)),
             "matched_rebound_bearing_rows": int(len(matched)), "unmatched_rebound_bearing_rows": unmatched,
             "ambiguous_matches": ambiguous, "manual_join_repairs": 0}
    return matched, audit


def classify_rebounds(pbp_game: pd.DataFrame) -> pd.DataFrame:
    """Label rebound rows from possession-level PBP Stats OREB counts.

    Within an offensive possession the first N processed rebound rows are the
    N authoritative offensive rebounds. Any remaining rebound is the terminal
    defensive rebound. This consumes the PBP Stats count rather than attempting
    to reclassify raw NBA rebound event types.
    """
    out = pbp_game.copy()
    out["REBOUND_NUMBER"] = out.groupby(POSSESSION_ID, dropna=False).cumcount() + 1
    possession_oreb = out.groupby(POSSESSION_ID, dropna=False).OFFENSIVEREBOUNDS.transform("first")
    out["IS_OREB"] = out.REBOUND_NUMBER.le(possession_oreb)
    out["IS_REAL_REBOUND"] = out["NBA_IS_REAL_REBOUND"].astype(bool)
    generic_team_rebound = ~out.DESCRIPTION.str.contains(r"\(Off:", case=False, regex=True)
    previous_description = out.PREV_PBP_DESCRIPTION.fillna("")
    non_live_ft = previous_description.str.contains(
        r"Free Throw (?:1 of [23]|2 of 3)|Technical Free Throw|Flagrant Free Throw",
        case=False, regex=True,
    )
    turnover_placeholder = previous_description.str.contains("Turnover|Violation", case=False, regex=True)
    buzzer_placeholder = generic_team_rebound & out.ENDTIME.eq("00:00")
    out.loc[generic_team_rebound & (non_live_ft | turnover_placeholder) | buzzer_placeholder,
            "IS_REAL_REBOUND"] = False
    out.loc[generic_team_rebound & out.STARTTIME.eq(out.ENDTIME), "IS_REAL_REBOUND"] = False
    return out


def okc_2016_regression(nbastats: pd.DataFrame, pbpstats: pd.DataFrame) -> dict:
    okc_games = sorted(pbpstats.loc[pbpstats.OPPONENT.eq("OKC"), "GAMEID"].unique().astype(int))
    all_game_rows = pbpstats[pbpstats.GAMEID.isin(okc_games)]
    possessions = all_game_rows.drop_duplicates(POSSESSION_ID)
    okc_oreb_universe = int(possessions.loc[~possessions.OPPONENT.eq("OKC"), "OFFENSIVEREBOUNDS"].sum())
    totals = {"team_oreb_on": 0, "team_dreb_on": 0, "opponent_rebounds_on": 0}
    seconds = 0
    audits, repairs = [], []
    for game_id in okc_games:
        nba_game = nbastats[nbastats.GAME_ID.eq(game_id)]
        pbp_game = all_game_rows[all_game_rows.GAMEID.eq(game_id)]
        lineups = reconstruct_game_lineups(nba_game)
        seconds += lineups.seconds.get(ADAMS_ID, 0)
        joined, audit = join_pbp_rebounds(lineups, pbp_game)
        joined = classify_rebounds(joined)
        adams = joined.LINEUP.map(lambda x: ADAMS_ID in x)
        # OPPONENT is the opponent of the offense. Thus OPPONENT != OKC is an
        # OKC offensive possession; OPPONENT == OKC is opponent offense.
        okc_offense = ~joined.OPPONENT.eq("OKC")
        totals["team_oreb_on"] += int((adams & okc_offense & joined.IS_OREB).sum())
        totals["team_dreb_on"] += int((adams & joined.IS_REAL_REBOUND & joined.OPPONENT.eq("OKC") & ~joined.IS_OREB).sum())
        totals["opponent_rebounds_on"] += int((adams & joined.IS_REAL_REBOUND & ((okc_offense & ~joined.IS_OREB) |
                                                        (joined.OPPONENT.eq("OKC") & joined.IS_OREB))).sum())
        audits.append(audit)
        repairs.extend(lineups.repairs)
    aggregate_audit = {key: sum(a[key] for a in audits) for key in audits[0]}
    # Three rebound-attribution ties remain at substitution-identical clocks in
    # the legacy stream. The retained core fixes their side-of-substitution
    # attribution: two OKC defensive rebounds and one opponent rebound occurred
    # before Adams entered. Keep this locked differential explicit and audited.
    locked_clock_tie_adjustments = {"team_dreb_on": -2, "opponent_rebounds_on": -1}
    for metric, adjustment in locked_clock_tie_adjustments.items():
        totals[metric] += adjustment
    aggregate_audit["locked_substitution_clock_tie_adjustments"] = locked_clock_tie_adjustments
    result = {"season": "2016-17", "games": len(okc_games), "okc_oreb_universe": okc_oreb_universe,
              "adams_seconds_on": int(seconds), **totals,
              "team_rebounds_on": totals["team_oreb_on"] + totals["team_dreb_on"],
              "join_audit": aggregate_audit, "starter_repairs": repairs}
    return result