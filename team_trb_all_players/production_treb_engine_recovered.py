#!/usr/bin/env python3
"""Production-only recovery layer for ambiguous legacy period-opening lineups.

The validated core and exact 2016 Adams regression are left unchanged. This
module calls the locked inference first and only attempts a recovery after that
inference raises.

Two evidence-only legacy artifacts are recoverable without guessing:
1. a bench player whose entire period participation is technical/ejection rows;
2. a player whose entire recorded participation occurs *after* the NBA Stats
   End Period event at the horn (post-period bookkeeping).

If excluding only those non-floor candidates leaves exactly five players for a
team, those five are accepted. No prior-quarter ending lineup is used to choose
among candidates. States that remain ambiguous still hard-fail.
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


def _player_rows(period: pd.DataFrame, player: int) -> pd.DataFrame:
    p1 = pd.to_numeric(period.PLAYER1_ID, errors="coerce")
    p2 = pd.to_numeric(period.PLAYER2_ID, errors="coerce")
    p3 = pd.to_numeric(period.PLAYER3_ID, errors="coerce")
    return period[p1.eq(player) | p2.eq(player) | p3.eq(player)].sort_values(["ELAPSED", "EVENTNUM"], kind="stable")


def _technical_or_ejection_only(period: pd.DataFrame, player: int) -> tuple[bool, list[dict]]:
    rows = _player_rows(period, player)
    if rows.empty:
        return False, []

    evidence = []
    for _, row in rows.iterrows():
        event_type = int(row.EVENTMSGTYPE)
        desc = _row_description(row)
        event_num = int(row.EVENTNUM)
        record = {"event_num": event_num, "event_type": event_type, "description": desc}
        evidence.append(record)

        if event_type == 8:
            return False, evidence
        if event_type == 11:
            continue
        if event_type == 6 and ("technical" in desc or "t.foul" in desc):
            continue
        return False, evidence

    return True, evidence


def _post_period_end_only(period: pd.DataFrame, player: int) -> tuple[bool, list[dict]]:
    """True only when every player row occurs after the recorded End Period event.

    This deliberately uses EVENTNUM ordering, not clock==0 alone. Legitimate
    horn events at 0:00 before the End Period marker remain normal evidence.
    """
    end_rows = period[pd.to_numeric(period.EVENTMSGTYPE, errors="coerce").eq(13)].sort_values("EVENTNUM", kind="stable")
    if end_rows.empty:
        return False, []
    end_event = int(end_rows.iloc[0].EVENTNUM)
    rows = _player_rows(period, player)
    if rows.empty:
        return False, []

    evidence = []
    for _, row in rows.iterrows():
        event_num = int(row.EVENTNUM)
        evidence.append({
            "event_num": event_num,
            "event_type": int(row.EVENTMSGTYPE),
            "clock": str(row.get("PCTIMESTRING", "")),
            "description": _row_description(row),
            "end_period_event_num": end_event,
        })
        if event_num <= end_event:
            return False, evidence
    return True, evidence


def _recover_team_starters(
    period: pd.DataFrame,
    team: int,
    team_candidates: set[int],
) -> tuple[set[int] | None, str | None, dict]:
    if len(team_candidates) <= 5:
        return None, None, {"reason": "not an over-complete candidate set"}

    excluded: dict[int, dict] = {}
    for player in sorted(team_candidates):
        is_tech_only, tech_evidence = _technical_or_ejection_only(period, player)
        if is_tech_only:
            excluded[player] = {
                "reason": "technical_ejection_only",
                "evidence": tech_evidence,
            }
            continue
        is_post_end_only, end_evidence = _post_period_end_only(period, player)
        if is_post_end_only:
            excluded[player] = {
                "reason": "post_period_end_bookkeeping_only",
                "evidence": end_evidence,
            }

    recovered = team_candidates - set(excluded)
    if len(recovered) == 5 and excluded:
        return recovered, "exclude_nonfloor_legacy_participants", {
            "excluded_players": sorted(excluded),
            "excluded_evidence": {str(k): v for k, v in excluded.items()},
        }

    return None, None, {
        "excluded_nonfloor_candidates": sorted(excluded),
        "excluded_evidence": {str(k): v for k, v in excluded.items()},
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


# core.reconstruct_game_lineups resolves this global at runtime. Patch only in
# this production-recovery module; validated source files remain unchanged.
core.infer_period_starters = infer_period_starters_recovered

reconstruct_game_lineups = base.reconstruct_game_lineups
join_pbp_rebounds = base.join_pbp_rebounds
classify_rebounds = base.classify_rebounds
