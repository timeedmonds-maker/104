#!/usr/bin/env python3
"""Local lineup reconstruction for NBA CDN play-by-play feeds (2024+).

Design goals:
- no NBA API dependency;
- use explicit CDN substitution-in / substitution-out rows;
- infer only the first-quarter opening five from local participation evidence;
- carry the previous period's ending five into later periods and apply explicit
  period-start substitutions at 12:00 / 5:00 before timing the period;
- never treat technical/ejection-only participation as proof a player was on court;
- hard-fail any state that does not resolve to exactly five players per team.

The engine is intentionally independent of the historical TREB production
engine so it can be validated against 2024 before being used for 2025-26.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

import pandas as pd

PLAYER_MAX = 1610612737


def _norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def clock_remaining_seconds(value: object) -> float:
    text = str(value)
    m = re.fullmatch(r"PT(?:(\d+)M)?([0-9.]+)S", text)
    if not m:
        raise ValueError(f"unsupported CDN clock {text!r}")
    return 60.0 * int(m.group(1) or 0) + float(m.group(2))


def period_length(period: int) -> float:
    return 720.0 if int(period) <= 4 else 300.0


def period_start_elapsed(period: int) -> float:
    p = int(period)
    return (p - 1) * 720.0 if p <= 4 else 2880.0 + (p - 5) * 300.0


def absolute_elapsed(period: int, clock: object) -> float:
    return period_start_elapsed(int(period)) + period_length(int(period)) - clock_remaining_seconds(clock)


def parse_person_ids(value: object) -> list[int]:
    if pd.isna(value):
        return []
    out = []
    for token in re.findall(r"\d+", str(value)):
        pid = int(token)
        if 0 < pid < PLAYER_MAX:
            out.append(pid)
    return out


def _is_technical_or_ejection(row: pd.Series) -> bool:
    action = _norm(row.get("actionType"))
    subtype = _norm(row.get("subType"))
    desc = _norm(row.get("description"))
    if action == "ejection" or "eject" in desc:
        return True
    if action == "foul" and ("technical" in subtype or "technical" in desc or " t.foul" in desc):
        return True
    return False


def _is_administrative(row: pd.Series) -> bool:
    action = _norm(row.get("actionType"))
    if action in {"substitution", "timeout", "period", "game", "replay", "instant replay", "ejection"}:
        return True
    if _is_technical_or_ejection(row):
        return True
    return False


def _prepare_game(game: pd.DataFrame) -> pd.DataFrame:
    g = game.copy()
    required = ["gameId", "period", "clock", "actionNumber", "actionType", "subType", "personId", "teamId"]
    missing = [c for c in required if c not in g.columns]
    if missing:
        raise ValueError(f"CDN feed missing required columns: {missing}")
    if "orderNumber" not in g.columns:
        g["orderNumber"] = g["actionNumber"]
    if "personIdsFilter" not in g.columns:
        g["personIdsFilter"] = pd.NA
    for col in ("gameId", "period", "actionNumber", "orderNumber", "personId", "teamId"):
        g[col] = pd.to_numeric(g[col], errors="coerce")
    g["period"] = g.period.astype(int)
    g["actionNumber"] = g.actionNumber.astype(int)
    g["orderNumber"] = g.orderNumber.fillna(g.actionNumber).astype(int)
    g["ELAPSED"] = [absolute_elapsed(int(p), c) for p, c in zip(g.period, g.clock)]
    return g.sort_values(["period", "ELAPSED", "orderNumber", "actionNumber"], kind="stable").reset_index(drop=True)


def _player_team_map(game: pd.DataFrame) -> dict[int, int]:
    evidence: dict[int, list[int]] = {}
    for _, row in game.iterrows():
        pid = int(row.personId) if pd.notna(row.personId) else 0
        team = int(row.teamId) if pd.notna(row.teamId) else 0
        if 0 < pid < PLAYER_MAX and team > 0:
            evidence.setdefault(pid, []).append(team)
    return {pid: max(set(teams), key=teams.count) for pid, teams in evidence.items()}


def _first_quarter_starters(period: pd.DataFrame, player_team: dict[int, int], teams: list[int]) -> tuple[dict[int, set[int]], list[dict]]:
    first_evidence: dict[int, str] = {}
    evidence_rows: dict[int, dict] = {}

    def register(pid: int, kind: str, row: pd.Series) -> None:
        if pid <= 0 or pid >= PLAYER_MAX or pid not in player_team or pid in first_evidence:
            return
        first_evidence[pid] = kind
        evidence_rows[pid] = {
            "actionNumber": int(row.actionNumber),
            "clock": str(row.clock),
            "actionType": str(row.actionType),
            "subType": str(row.subType),
            "description": str(row.get("description", "")),
        }

    for _, row in period.iterrows():
        action = _norm(row.actionType)
        subtype = _norm(row.subType)
        pid = int(row.personId) if pd.notna(row.personId) else 0
        if action == "substitution":
            if subtype not in {"in", "out"}:
                raise ValueError(f"unknown CDN substitution subtype game={int(row.gameId)} action={int(row.actionNumber)}: {row.subType!r}")
            register(pid, subtype, row)
            continue
        if _is_administrative(row):
            continue
        register(pid, "play", row)
        for extra in parse_person_ids(row.personIdsFilter):
            register(extra, "play", row)

    starters: dict[int, set[int]] = {}
    audit = []
    for team in teams:
        chosen = {pid for pid, kind in first_evidence.items() if player_team.get(pid) == team and kind in {"play", "out"}}
        if len(chosen) != 5:
            details = {pid: {"kind": first_evidence.get(pid), "evidence": evidence_rows.get(pid)} for pid in sorted(first_evidence) if player_team.get(pid) == team}
            raise ValueError(f"unresolved CDN first-quarter starters game={int(period.gameId.iloc[0])} team={team}: {sorted(chosen)} evidence={details}")
        starters[team] = chosen
        audit.append({"period": 1, "team_id": team, "method": "first_local_evidence_before_entry", "starters": sorted(chosen)})
    return starters, audit


def _apply_substitution_group(lineups: dict[int, set[int]], rows: pd.DataFrame, player_team: dict[int, int], game_id: int, period: int, clock: str) -> list[dict]:
    changes = []
    subs = rows[rows.actionType.astype("string").fillna("").str.lower().eq("substitution")].copy()
    if subs.empty:
        return changes

    # CDN represents paired substitutions as separate rows. Apply all outs first
    # and all ins second at the same game clock, then validate the five-player state.
    for wanted in ("out", "in"):
        selected = subs[subs.subType.astype("string").fillna("").str.lower().eq(wanted)]
        for _, row in selected.sort_values(["orderNumber", "actionNumber"], kind="stable").iterrows():
            pid = int(row.personId) if pd.notna(row.personId) else 0
            team = int(row.teamId) if pd.notna(row.teamId) else player_team.get(pid, 0)
            if pid <= 0 or team not in lineups:
                raise ValueError(f"invalid CDN substitution game={game_id} period={period} action={int(row.actionNumber)} player={pid} team={team}")
            if wanted == "out":
                if pid not in lineups[team]:
                    raise ValueError(f"CDN substitution outgoing absent game={game_id} period={period} action={int(row.actionNumber)} player={pid} lineup={sorted(lineups[team])}")
                lineups[team].remove(pid)
            else:
                if pid in lineups[team]:
                    raise ValueError(f"CDN substitution incoming already present game={game_id} period={period} action={int(row.actionNumber)} player={pid}")
                lineups[team].add(pid)
            changes.append({"actionNumber": int(row.actionNumber), "clock": clock, "team_id": team, "player_id": pid, "direction": wanted})

    for team, lineup in lineups.items():
        if len(lineup) != 5:
            raise ValueError(f"CDN lineup size {len(lineup)} after substitutions game={game_id} period={period} clock={clock} team={team}: {sorted(lineup)}")
    return changes


@dataclass
class ModernGameLineups:
    events: pd.DataFrame
    seconds: dict[int, float]
    repairs: list[dict]
    teams: list[int]


def reconstruct_game_lineups(game: pd.DataFrame) -> ModernGameLineups:
    g = _prepare_game(game)
    if g.empty:
        raise ValueError("empty CDN game")
    game_id = int(g.gameId.iloc[0])
    player_team = _player_team_map(g)
    teams = sorted({int(x) for x in g.teamId.dropna().astype(int) if int(x) > 0})
    # Administrative rows can contain non-NBA team identifiers, but the two
    # overwhelmingly represented team IDs are the game teams. Resolve by count.
    if len(teams) != 2:
        counts = g.loc[g.teamId.notna() & g.personId.notna()].teamId.astype(int).value_counts()
        teams = [int(x) for x in counts.head(2).index]
    if len(teams) != 2:
        raise ValueError(f"expected two CDN teams game={game_id}, got {teams}")

    seconds: dict[int, float] = {}
    snapshots = []
    audit: list[dict] = []
    lineups: dict[int, set[int]] | None = None

    for period_number, period in g.groupby("period", sort=True):
        period_number = int(period_number)
        period = period.sort_values(["ELAPSED", "orderNumber", "actionNumber"], kind="stable").copy()
        start = period_start_elapsed(period_number)
        end = start + period_length(period_number)

        if period_number == 1:
            lineups, first_audit = _first_quarter_starters(period, player_team, teams)
            audit.extend(first_audit)
        else:
            if lineups is None:
                raise ValueError(f"missing prior CDN lineup game={game_id} period={period_number}")
            lineups = {team: set(players) for team, players in lineups.items()}

        # Apply substitutions timestamped exactly at the period opening before
        # the first interval. This encodes quarter/half-time lineup changes.
        opening = period[period.ELAPSED.sub(start).abs().le(0.011)]
        if len(opening):
            changes = _apply_substitution_group(lineups, opening, player_team, game_id, period_number, str(opening.clock.iloc[0]))
            if changes:
                audit.append({"period": period_number, "type": "period_opening_substitutions", "changes": changes})

        for team in teams:
            if len(lineups.get(team, set())) != 5:
                raise ValueError(f"invalid CDN opening five game={game_id} period={period_number} team={team}: {sorted(lineups.get(team, set()))}")

        last = start
        period_rows = []
        for now, group in period.groupby("ELAPSED", sort=True):
            now = float(now)
            if now > last + 1e-9:
                delta = now - last
                for players in lineups.values():
                    for pid in players:
                        seconds[pid] = seconds.get(pid, 0.0) + delta
                last = now

            is_opening = abs(now - start) <= 0.011
            changes = [] if is_opening else _apply_substitution_group(lineups, group, player_team, game_id, period_number, str(group.clock.iloc[0]))

            lineup_tuple = tuple(sorted(set().union(*lineups.values())))
            if len(lineup_tuple) != 10:
                raise ValueError(f"invalid CDN ten-player state game={game_id} period={period_number} clock={group.clock.iloc[0]}: {lineup_tuple}")
            for _, row in group.sort_values(["orderNumber", "actionNumber"], kind="stable").iterrows():
                item = row.to_dict()
                item["LINEUP"] = lineup_tuple
                period_rows.append(item)
            if changes:
                audit.append({"period": period_number, "type": "substitutions", "elapsed": now, "changes": changes})

        if end > last + 1e-9:
            delta = end - last
            for players in lineups.values():
                for pid in players:
                    seconds[pid] = seconds.get(pid, 0.0) + delta
        snapshots.append(pd.DataFrame(period_rows))

    events = pd.concat(snapshots, ignore_index=True) if snapshots else g.iloc[0:0].copy()
    expected_total = sum(period_length(int(p)) for p in sorted(g.period.unique())) * 10.0
    observed_total = sum(seconds.values())
    if abs(observed_total - expected_total) > 0.05:
        raise ValueError(f"CDN player-seconds total mismatch game={game_id}: observed={observed_total:.3f} expected={expected_total:.3f}")
    return ModernGameLineups(events=events, seconds=seconds, repairs=audit, teams=teams)
