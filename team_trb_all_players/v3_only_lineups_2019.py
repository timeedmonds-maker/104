#!/usr/bin/env python3
"""Reconstruct 2019 NBA lineups from V3 when the legacy event feed is absent.

V3 2019 records each substitution as one descriptive row (``SUB: IN FOR OUT``)
whose personId is the outgoing player.  This module:
1. resolves the incoming player from same-game V3 name evidence;
2. expands each substitution into an ordered OUT/IN pair at zero elapsed time;
3. independently solves each period's opening five from that period's player
   actions and legal substitution constraints;
4. uses the previous period's ending lineup only to break otherwise-equivalent
   opening-five solutions;
5. replays the solved period and requires exactly five players per team whenever
   time accrues or a statistical action occurs.

This avoids the 2025-CDN assumption that the end-of-quarter five carries into the
next period.  Older V3 feeds do not always encode quarter-break substitutions.
No roster/minute guess is used to create a lineup; ambiguity hard-fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
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

    # V3 substitution descriptions can use initials while playerName contains a
    # surname only. Permit suffix matching only when unique within the team.
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
            "source_action_number": int(row.sourceActionNumber) if "sourceActionNumber" in row else int(row.actionNumber),
            "action_id": int(row.actionId),
            "clock": str(row.clock),
            "team_id": int(row.teamId),
            "outgoing_player_id": outgoing,
            "incoming_player_id": incoming,
            "description": str(row.get("description", "")),
        })

    expanded = pd.DataFrame(rows)
    # Reuse the modern module's clock conversion and stable preparation, but not
    # its quarter-to-quarter carry rule.
    prepared = modern._prepare_game(expanded)
    return prepared, audit


def _is_player_action(row: pd.Series) -> bool:
    if modern._is_administrative(row):
        return False
    pid = int(row.personId) if pd.notna(row.personId) else 0
    return 0 < pid < PLAYER_MAX


def _team_period_pool(period: pd.DataFrame, team: int, player_team: dict[int, int], prior: set[int] | None) -> set[int]:
    participants: set[int] = set()
    sub_in: set[int] = set()
    sub_out: set[int] = set()
    first_sub: dict[int, str] = {}

    ordered = period.sort_values(["ELAPSED", "orderNumber", "actionNumber"], kind="stable")
    for _, row in ordered.iterrows():
        pid = int(row.personId) if pd.notna(row.personId) else 0
        if not (0 < pid < PLAYER_MAX and player_team.get(pid) == team):
            continue
        action = display_norm(row.actionType).lower()
        subtype = display_norm(row.subType).lower()
        if action == "substitution":
            participants.add(pid)
            if subtype == "in":
                sub_in.add(pid)
            elif subtype == "out":
                sub_out.add(pid)
            first_sub.setdefault(pid, subtype)
        elif _is_player_action(row):
            participants.add(pid)

    # A player whose first substitution evidence is IN cannot have opened the
    # period; a first OUT must have opened unless a feed anomaly exists.
    pool = set(participants)
    for pid in sub_in - sub_out:
        pool.discard(pid)
    for pid in sub_in & sub_out:
        if first_sub.get(pid) == "in":
            pool.discard(pid)
        else:
            pool.add(pid)

    # A player can remain on court for an entire quarter without a personal box
    # event. Prior ending players are eligible only when they are not explicitly
    # first observed entering this period.
    if prior:
        for pid in prior:
            if player_team.get(pid) == team and first_sub.get(pid) != "in":
                pool.add(pid)
    return pool


def _simulate_team(period: pd.DataFrame, team: int, starters: set[int], player_team: dict[int, int]) -> tuple[bool, list[str], set[int]]:
    lineup = set(starters)
    violations: list[str] = []
    ordered = period.sort_values(["ELAPSED", "orderNumber", "actionNumber"], kind="stable")
    for _, row in ordered.iterrows():
        pid = int(row.personId) if pd.notna(row.personId) else 0
        action = display_norm(row.actionType).lower()
        subtype = display_norm(row.subType).lower()
        if action == "substitution" and player_team.get(pid) == team:
            if subtype == "out":
                if pid not in lineup:
                    violations.append(f"out_absent:{int(row.actionNumber)}:{pid}")
                else:
                    lineup.remove(pid)
            elif subtype == "in":
                if pid in lineup:
                    violations.append(f"in_present:{int(row.actionNumber)}:{pid}")
                else:
                    lineup.add(pid)
            if len(lineup) not in {4, 5, 6}:
                violations.append(f"sub_lineup_size:{int(row.actionNumber)}:{len(lineup)}")
            continue

        if player_team.get(pid) == team and _is_player_action(row) and pid not in lineup:
            violations.append(f"participant_absent:{int(row.actionNumber)}:{pid}")
        if action != "substitution" and len(lineup) != 5:
            violations.append(f"live_lineup_size:{int(row.actionNumber)}:{len(lineup)}")
    return not violations, violations, lineup


def solve_period_starters(
    period: pd.DataFrame,
    team: int,
    player_team: dict[int, int],
    prior_end: set[int] | None,
) -> tuple[set[int], dict]:
    game_id = int(period.gameId.iloc[0])
    period_number = int(period.period.iloc[0])
    pool = _team_period_pool(period, team, player_team, prior_end)

    # The constraint pool can contain more than five players if a same-period
    # feed correction makes first evidence ambiguous. Enumerate legal fives only.
    if len(pool) < 5:
        raise ValueError(
            f"V3 period starter pool underfull game={game_id} period={period_number} team={team}: {sorted(pool)}"
        )
    if len(pool) > 12:
        raise ValueError(
            f"V3 period starter pool unexpectedly large game={game_id} period={period_number} team={team}: {sorted(pool)}"
        )

    solutions = []
    for combo in combinations(sorted(pool), 5):
        legal, violations, end_lineup = _simulate_team(period, team, set(combo), player_team)
        if legal:
            prior_distance = len(set(combo) ^ set(prior_end or set())) if prior_end is not None else 0
            solutions.append((prior_distance, tuple(combo), tuple(sorted(end_lineup))))

    if not solutions:
        # Include the best few failed candidates for auditability.
        ranked = []
        for combo in combinations(sorted(pool), 5):
            legal, violations, _ = _simulate_team(period, team, set(combo), player_team)
            ranked.append((len(violations), tuple(combo), violations[:10]))
        ranked.sort(key=lambda x: (x[0], x[1]))
        raise ValueError(
            f"no legal V3 period starter solution game={game_id} period={period_number} team={team}: "
            f"pool={sorted(pool)} best={ranked[:5]}"
        )

    solutions.sort(key=lambda x: (x[0], x[1]))
    best_distance = solutions[0][0]
    best = [x for x in solutions if x[0] == best_distance]
    if len(best) != 1:
        raise ValueError(
            f"non-unique V3 period starter solution game={game_id} period={period_number} team={team}: "
            f"pool={sorted(pool)} prior={sorted(prior_end or set())} solutions={[list(x[1]) for x in best[:20]]}"
        )
    chosen = set(best[0][1])
    return chosen, {
        "period": period_number,
        "team_id": team,
        "method": "period_local_event_and_substitution_constraints",
        "candidate_pool": sorted(pool),
        "prior_end": sorted(prior_end or set()),
        "starters": sorted(chosen),
        "legal_solution_count": len(solutions),
        "best_prior_distance": best_distance,
    }


@dataclass
class V3OnlyLineups:
    events: pd.DataFrame
    seconds: dict[int, float]
    repairs: list[dict]
    teams: list[int]
    substitution_expansion_audit: list[dict]


def reconstruct_game_lineups(game: pd.DataFrame) -> V3OnlyLineups:
    g, expansion_audit = expand_substitutions(game)
    if g.empty:
        raise ValueError("empty V3 game")
    game_id = int(g.gameId.iloc[0])
    player_team = modern._player_team_map(g)
    teams = sorted({int(x) for x in g.teamId.dropna().astype(int) if int(x) > 0})
    if len(teams) != 2:
        counts = g.loc[g.teamId.notna() & g.personId.notna()].teamId.astype(int).value_counts()
        teams = [int(x) for x in counts.head(2).index]
    if len(teams) != 2:
        raise ValueError(f"expected two V3 teams game={game_id}, got {teams}")

    seconds: dict[int, float] = {}
    snapshots: list[pd.DataFrame] = []
    audit: list[dict] = []
    prior_end: dict[int, set[int]] = {}

    for period_number, period in g.groupby("period", sort=True):
        period_number = int(period_number)
        period = period.sort_values(["ELAPSED", "orderNumber", "actionNumber"], kind="stable").copy()
        lineups: dict[int, set[int]] = {}
        for team in teams:
            starters, starter_audit = solve_period_starters(period, team, player_team, prior_end.get(team))
            lineups[team] = starters
            audit.append(starter_audit)

        modern._validate_five(lineups, teams, game_id, period_number, str(period.iloc[0].clock), "at V3 period start")
        start = modern.period_start_elapsed(period_number)
        end = start + modern.period_length(period_number)
        last = start
        period_rows: list[dict] = []

        for now, group in period.groupby("ELAPSED", sort=True):
            now = float(now)
            if now > last + 1e-9:
                modern._validate_five(lineups, teams, game_id, period_number, str(group.iloc[0].clock), "before V3 elapsed interval")
                delta = now - last
                for players in lineups.values():
                    for pid in players:
                        seconds[pid] = seconds.get(pid, 0.0) + delta
                last = now

            ordered = group.sort_values(["orderNumber", "actionNumber"], kind="stable")
            changes = []
            for _, row in ordered.iterrows():
                action = display_norm(row.actionType).lower()
                if action == "substitution":
                    change = modern._apply_one_substitution(lineups, row, player_team, game_id, period_number)
                    changes.append(change)
                    item = row.to_dict()
                    item["LINEUP"] = tuple(sorted(set().union(*(lineups[t] for t in teams))))
                    period_rows.append(item)
                    continue
                modern._validate_five(lineups, teams, game_id, period_number, str(row.clock), f"at V3 action {int(row.actionNumber)}")
                pid = int(row.personId) if pd.notna(row.personId) else 0
                if _is_player_action(row) and pid in player_team:
                    team = player_team[pid]
                    if team in lineups and pid not in lineups[team]:
                        raise ValueError(
                            f"V3 participant absent after solved start game={game_id} period={period_number} "
                            f"action={int(row.actionNumber)} player={pid} lineup={sorted(lineups[team])}"
                        )
                item = row.to_dict()
                item["LINEUP"] = tuple(sorted(set().union(*(lineups[t] for t in teams))))
                period_rows.append(item)
            modern._validate_five(lineups, teams, game_id, period_number, str(ordered.iloc[-1].clock), "at V3 timestamp end")
            if changes:
                audit.append({
                    "period": period_number,
                    "type": "expanded_v3_substitution_block",
                    "elapsed": now,
                    "changes": changes,
                })

        if end > last + 1e-9:
            modern._validate_five(lineups, teams, game_id, period_number, "00:00", "before V3 period end")
            delta = end - last
            for players in lineups.values():
                for pid in players:
                    seconds[pid] = seconds.get(pid, 0.0) + delta
        prior_end = {team: set(lineups[team]) for team in teams}
        snapshots.append(pd.DataFrame(period_rows))

    observed_total = sum(seconds.values())
    expected_total = sum(modern.period_length(int(p)) for p in sorted(g.period.unique())) * 10.0
    if abs(observed_total - expected_total) > 0.05:
        raise ValueError(
            f"V3 player-seconds total mismatch game={game_id}: observed={observed_total:.3f} expected={expected_total:.3f}"
        )
    events = pd.concat(snapshots, ignore_index=True) if snapshots else g.iloc[0:0].copy()
    return V3OnlyLineups(
        events=events,
        seconds=seconds,
        repairs=audit,
        teams=teams,
        substitution_expansion_audit=expansion_audit,
    )
