#!/usr/bin/env python3
"""Production-only recovery layer for ambiguous legacy period-opening lineups.

The validated core and the exact 2016 Adams regression implementation are left
unchanged.  This module first calls the locked starter inference verbatim and
only attempts a recovery if that inference raises.  Recovery is deliberately
conservative: it accepts a lineup only when independent local evidence reduces
one team's candidate state to exactly five players.
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
        first = subs[(subs.PLAYER1_ID.eq(player)) | (subs.PLAYER2_ID.eq(player))].iloc[0]
        if int(first.PLAYER2_ID) == player:
            candidates.discard(player)
        else:
            candidates.add(player)
    return player_team, subs, candidates


def _first_sub_role(subs: pd.DataFrame, player: int) -> str | None:
    rows = subs[(subs.PLAYER1_ID.eq(player)) | (subs.PLAYER2_ID.eq(player))]
    if rows.empty:
        return None
    first = rows.iloc[0]
    return "in" if int(first.PLAYER2_ID) == int(player) else "out"


def _first_appearance_role(period: pd.DataFrame, player: int) -> str | None:
    ordered = period.sort_values(["ELAPSED", "EVENTNUM"], kind="stable")
    for _, row in ordered.iterrows():
        event_type = int(row.EVENTMSGTYPE)
        p1 = int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
        p2 = int(row.PLAYER2_ID) if pd.notna(row.PLAYER2_ID) else 0
        p3 = int(row.PLAYER3_ID) if pd.notna(row.PLAYER3_ID) else 0
        if player not in (p1, p2, p3):
            continue
        if event_type == 8 and p2 == player:
            return "in"
        if event_type == 8 and p1 == player:
            return "out"
        return "play"
    return None


def _recover_team_starters(
    period: pd.DataFrame,
    team: int,
    team_candidates: set[int],
    player_team: dict[int, int],
    subs: pd.DataFrame,
    prior_lineups: dict[int, set[int]] | None,
) -> tuple[set[int] | None, str | None, dict]:
    prior = set(prior_lineups.get(team, set())) if prior_lineups else set()

    # Strongest recovery: the ambiguous legacy period contains six apparent
    # starters, but exactly five were also the known five on court at the end
    # of the preceding period.  No arbitrary truncation is permitted.
    if prior:
        common = team_candidates & prior
        if len(common) == 5:
            return common, "prior_period_end_intersection", {
                "prior_lineup": sorted(prior),
                "intersection": sorted(common),
            }

        # Some overtime/legacy periods omit a player who generates no event in
        # the period.  Carry a prior-period player only when the substitution
        # chronology does not say that player's first action was entering.
        if len(team_candidates) < 5:
            definite = {
                p for p in prior - team_candidates
                if _first_sub_role(subs, p) == "out"
            }
            if len(team_candidates | definite) == 5:
                recovered = team_candidates | definite
                return recovered, "prior_period_first_sub_out", {
                    "prior_lineup": sorted(prior),
                    "carried": sorted(definite),
                }
            eligible = {
                p for p in prior - team_candidates
                if _first_sub_role(subs, p) != "in"
            }
            if len(team_candidates | eligible) == 5:
                recovered = team_candidates | eligible
                return recovered, "prior_period_non_entry_carry", {
                    "prior_lineup": sorted(prior),
                    "carried": sorted(eligible),
                }

    # Differential fallback used by the previously validated 2016 reference:
    # a player whose first appearance is a substitution-in did not open the
    # period.  Accept only if this independently produces exactly five.
    team_players = {p for p, t in player_team.items() if t == team}
    first_appearance = {
        p for p in team_players
        if _first_appearance_role(period, p) != "in"
    }
    if len(first_appearance) == 5:
        return first_appearance, "validated_first_appearance_fallback", {
            "first_appearance_candidates": sorted(first_appearance),
        }

    return None, None, {
        "prior_lineup": sorted(prior),
        "first_appearance_candidates": sorted(first_appearance),
    }


def infer_period_starters_recovered(
    period: pd.DataFrame,
    game_id: int,
    period_number: int,
    prior_lineups: dict[int, set[int]] | None = None,
):
    """Run locked inference first; recover only otherwise-unresolved states."""
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

            recovered, method, evidence = _recover_team_starters(
                period, team, team_candidates, player_team, subs, prior_lineups
            )
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
# this production-recovery module; the source file and regression script remain
# byte-for-byte untouched.
core.infer_period_starters = infer_period_starters_recovered

reconstruct_game_lineups = base.reconstruct_game_lineups
join_pbp_rebounds = base.join_pbp_rebounds
classify_rebounds = base.classify_rebounds
