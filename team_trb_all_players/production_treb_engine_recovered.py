#!/usr/bin/env python3
"""Production-only recovery layer for ambiguous legacy period-opening lineups.

The validated core and exact 2016 Adams regression are left unchanged.  This
module calls the locked inference first and only attempts a recovery after that
inference raises.

The principal recovery is evidence-based: legacy NBA play-by-play sometimes
attributes a technical foul/ejection to a bench player.  Those rows establish
team identity but do *not* establish that the player was on court.  If removing
only candidates whose entire period participation consists of technical/ejection
rows leaves exactly five candidates for the team, those five are accepted.

No prior-quarter lineup is used to choose among six candidates.  Quarter-break
lineup changes make that inference unsafe.  States that remain ambiguous still
fail and enter the repair queue.
"""
from __future__ import annotations

import pandas as pd

import production_treb_engine as base

core = base.core
_LOCKED_INFER_PERIOD_STARTERS = core.infer_period_starters


def _candidate_state(period: pd.DataFrame):
    player_team = core._player_team(period)
    participants = set(player_team)
    subs = period.loc[period.EVENTMSGTYPE.eq(8)].sort_values(["ELAPSED", "EVENTNUM"], kind="stable")
    sub_out = set(pd.to_numeric(subs.PLAYER1_ID, errors="coerce").dropna().astype(int))
    sub_in = set(pd.to_numeric(subs.PLAYER2_ID, errors="coerce").dropna().astype(int))
    candidates = participants - (sub_in - sub_out)
    for player in sub_in & sub_out:
        rows = subs[(subs.PLAYER1_ID.eq(player)) | (subs.PLAYER2_ID.eq(player))]
        first = rows.iloc[0]
        if int(first.PLAYER2_ID) == player:
            candidates.discard(player)
        else:
            candidates.add(player)
    return player_team, subs, candidates


def _row_description(row: pd.Series) -> str:
    return " ".join(
        str(row.get(c, ""))
        for c in ("HOMEDESCRIPTION", "NEUTRALDESCRIPTION", "VISITORDESCRIPTION")
        if pd.notna(row.get(c))
    ).lower()


def _technical_or_ejection_only(period: pd.DataFrame, player: int) -> tuple[bool, list[dict]]:
    p1 = pd.to_numeric(period.PLAYER1_ID, errors="coerce")
    p2 = pd.to_numeric(period.PLAYER2_ID, errors="coerce")
    p3 = pd.to_numeric(period.PLAYER3_ID, errors="coerce")
    rows = period[p1.eq(player) | p2.eq(player) | p3.eq(player)].sort_values(["ELAPSED", "EVENTNUM"], kind="stable")
    if rows.empty:
        return False, []

    evidence = []
    for _, row in rows.iterrows():
        event_type = int(row.EVENTMSGTYPE)
        desc = _row_description(row)
        event_num = int(row.EVENTNUM)
        record = {"event_num": event_num, "event_type": event_type, "description": desc}
        evidence.append(record)

        # A substitution or any ordinary live/statistical event is positive
        # evidence that the player participated on court during the period.
        if event_type == 8:
            return False, evidence

        # NBA Stats event type 11 is ejection.  Event type 6 covers fouls;
        # only explicit technical descriptions are non-on-court evidence.
        if event_type == 11:
            continue
        if event_type == 6 and ("technical" in desc or "t.foul" in desc):
            continue

        return False, evidence

    return True, evidence


def _recover_team_starters(
    period: pd.DataFrame,
    team: int,
    team_candidates: set[int],
) -> tuple[set[int] | None, str | None, dict]:
    if len(team_candidates) <= 5:
        return None, None, {"reason": "not an over-complete candidate set"}

    technical_only = {}
    for player in sorted(team_candidates):
        is_nonfloor_only, evidence = _technical_or_ejection_only(period, player)
        if is_nonfloor_only:
            technical_only[player] = evidence

    recovered = team_candidates - set(technical_only)
    if len(recovered) == 5 and technical_only:
        return recovered, "exclude_technical_ejection_only_participants", {
            "excluded_players": sorted(technical_only),
            "excluded_evidence": {str(k): v for k, v in technical_only.items()},
        }

    return None, None, {
        "technical_ejection_only_candidates": sorted(technical_only),
        "remaining_candidates": sorted(recovered),
    }


def infer_period_starters_recovered(
    period: pd.DataFrame,
    game_id: int,
    period_number: int,
    prior_lineups: dict[int, set[int]] | None = None,
):
    """Run locked inference first; recover only evidence-proven over-complete states."""
    try:
        return _LOCKED_INFER_PERIOD_STARTERS(period, game_id, period_number, prior_lineups)
    except ValueError as locked_error:
        player_team, subs, candidates = _candidate_state(period)
        teams = sorted(set(player_team.values()))
        starters: dict[int, set[int]] = {}
        repairs: list[dict] = []

        for team in teams:
            team_candidates = {p for p in candidates if player_team.get(p) == team}
            key = (game_id, period_number, team)
            if key in core.STARTER_REPAIRS:
                repaired = set(core.STARTER_REPAIRS[key])
                if len(repaired) != 5:
                    raise locked_error
                starters[team] = repaired
                repairs.append({
                    "game_id": game_id,
                    "period": period_number,
                    "team_id": team,
                    "type": "explicit_starter_repair",
                    "inferred_candidates": sorted(team_candidates),
                    "repaired_starters": sorted(repaired),
                    "evidence": "locked explicit production repair",
                })
                continue
            if len(team_candidates) == 5:
                starters[team] = team_candidates
                continue

            recovered, method, evidence = _recover_team_starters(period, team, team_candidates)
            if recovered is None or len(recovered) != 5:
                raise locked_error
            starters[team] = recovered
            repairs.append({
                "game_id": game_id,
                "period": period_number,
                "team_id": team,
                "type": "legacy_starter_recovery",
                "method": method,
                "inferred_candidates": sorted(team_candidates),
                "repaired_starters": sorted(recovered),
                "evidence": evidence,
            })

        if len(starters) != 2 or any(len(v) != 5 for v in starters.values()):
            raise locked_error
        return starters, repairs


# core.reconstruct_game_lineups resolves this global at runtime.  Patch only in
# this production-recovery module; validated source files remain unchanged.
core.infer_period_starters = infer_period_starters_recovered

reconstruct_game_lineups = base.reconstruct_game_lineups
join_pbp_rebounds = base.join_pbp_rebounds
classify_rebounds = base.classify_rebounds
