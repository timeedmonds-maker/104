#!/usr/bin/env python3
"""Reconstruct lineups from NBA Stats V3 when the legacy event feed is absent.

V3 2019 records substitutions as one descriptive row (``SUB: IN FOR OUT``)
whose personId is the outgoing player.  The already-validated modern lineup
engine expects explicit OUT and IN rows.  This adapter resolves the incoming
player from the same game's V3 player-name evidence, expands each substitution
into an ordered OUT/IN pair at zero elapsed time, and delegates all lineup and
seconds invariants to ``modern_cdn_lineups``.

No player is inferred by roster order or minutes.  Any ambiguous/missing name
resolution hard-fails the game and is emitted for repair.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd

import modern_cdn_lineups as modern

PLAYER_MAX = modern.PLAYER_MAX


def norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def display_norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _name_index(game: pd.DataFrame) -> dict[tuple[int, str], set[int]]:
    index: dict[tuple[int, str], set[int]] = {}
    for _, row in game.iterrows():
        pid = int(row.personId) if pd.notna(row.get("personId")) else 0
        team = int(row.teamId) if pd.notna(row.get("teamId")) else 0
        if not (0 < pid < PLAYER_MAX and team > 0):
            continue
        for col in ("playerName", "playerNameI"):
            if col not in game:
                continue
            key = norm(row.get(col))
            if key:
                index.setdefault((team, key), set()).add(pid)
    return index


def _incoming_label(description: object) -> str:
    text = display_norm(description)
    m = re.match(r"(?i)^SUB:\s*(.+?)\s+FOR\s+(.+?)\s*$", text)
    if not m:
        raise ValueError(f"unsupported V3 substitution description {text!r}")
    return m.group(1).strip()


def resolve_incoming(game: pd.DataFrame, row: pd.Series, index: dict[tuple[int, str], set[int]]) -> int:
    team = int(row.teamId) if pd.notna(row.teamId) else 0
    label = _incoming_label(row.get("description"))
    key = norm(label)
    hits = set(index.get((team, key), set()))
    if len(hits) == 1:
        return next(iter(hits))

    # V3 descriptions sometimes abbreviate given names while playerName is only
    # the surname.  Permit a suffix match only if it is unique within the team.
    suffix_hits: set[int] = set()
    for (tid, player_key), pids in index.items():
        if tid != team:
            continue
        if key.endswith(player_key) or player_key.endswith(key):
            suffix_hits.update(pids)
    if len(suffix_hits) == 1:
        return next(iter(suffix_hits))
    raise ValueError(
        f"cannot uniquely resolve V3 substitution incoming game={int(row.gameId)} "
        f"period={int(row.period)} action={int(row.actionNumber)} team={team} "
        f"label={label!r} exact={sorted(hits)} suffix={sorted(suffix_hits)}"
    )


def normalize_v3(game: pd.DataFrame) -> pd.DataFrame:
    g = game.copy()
    if "actionId" not in g:
        raise ValueError("V3 source missing actionId chronology")
    for col in ("gameId", "period", "actionNumber", "actionId", "personId", "teamId"):
        g[col] = pd.to_numeric(g[col], errors="coerce")
    g["gameId"] = g.gameId.astype("int64")
    g["period"] = g.period.astype("int64")
    g["actionNumber"] = g.actionNumber.astype("int64")
    g["actionId"] = g.actionId.astype("int64")
    return g.sort_values(["period", "actionId", "actionNumber"], kind="stable").reset_index(drop=True)


def expand_substitutions(game: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    g = normalize_v3(game)
    names = _name_index(g)
    rows: list[dict] = []
    audit: list[dict] = []

    for _, row in g.iterrows():
        action = display_norm(row.get("actionType")).lower()
        base = row.to_dict()
        # Give each V3 action a stable ordered range.  The source actionNumber is
        # preserved separately for event/rebound joins.
        base["sourceActionNumber"] = int(row.actionNumber)
        base["orderNumber"] = int(row.actionId) * 10
        base["personIdsFilter"] = pd.NA
        if action != "substitution":
            rows.append(base)
            continue

        outgoing = int(row.personId) if pd.notna(row.personId) else 0
        incoming = resolve_incoming(g, row, names)
        if outgoing <= 0 or outgoing >= PLAYER_MAX:
            raise ValueError(
                f"invalid V3 outgoing player game={int(row.gameId)} action={int(row.actionNumber)}: {outgoing}"
            )
        out_row = dict(base)
        out_row["subType"] = "out"
        out_row["personId"] = outgoing
        out_row["actionNumber"] = int(row.actionNumber) * 10
        out_row["orderNumber"] = int(row.actionId) * 10

        in_row = dict(base)
        in_row["subType"] = "in"
        in_row["personId"] = incoming
        in_row["playerName"] = _incoming_label(row.get("description"))
        in_row["actionNumber"] = int(row.actionNumber) * 10 + 1
        in_row["orderNumber"] = int(row.actionId) * 10 + 1

        rows.extend([out_row, in_row])
        audit.append({
            "game_id": int(row.gameId),
            "period": int(row.period),
            "source_action_number": int(row.actionNumber),
            "action_id": int(row.actionId),
            "clock": str(row.clock),
            "team_id": int(row.teamId),
            "outgoing_player_id": outgoing,
            "incoming_player_id": incoming,
            "description": str(row.get("description", "")),
        })

    expanded = pd.DataFrame(rows)
    return expanded, audit


@dataclass
class V3OnlyLineups:
    events: pd.DataFrame
    seconds: dict[int, float]
    repairs: list[dict]
    teams: list[int]
    substitution_expansion_audit: list[dict]


def reconstruct_game_lineups(game: pd.DataFrame) -> V3OnlyLineups:
    expanded, expansion_audit = expand_substitutions(game)
    result = modern.reconstruct_game_lineups(expanded)
    return V3OnlyLineups(
        events=result.events,
        seconds=result.seconds,
        repairs=result.repairs,
        teams=result.teams,
        substitution_expansion_audit=expansion_audit,
    )
