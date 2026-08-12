#!/usr/bin/env python3
"""V3-chronology, team-local repair layer for historical TREB reconstruction.

PBP Stats remains the authoritative rebound universe. Legacy NBA Stats rows
remain the rich participant/source event record. NBA Stats v3 is used only to
recover chronological ordering at identical clocks (via actionId) and to avoid
known legacy EVENTNUM ordering defects.

The important structural change from the original reconstruction is that team
lineups are carried directly from period to period. A silent player therefore
is not lost merely because he generates no player/team event evidence in the
next period.
"""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

import local_treb_rebuild as core
import production_treb_engine as legacy

BASE = Path(__file__).resolve().parent
EVIDENCE_PATH = BASE / "final_integrity_rebuild" / "legacy_starter_repair_evidence.json"
PLAYER_MAX = core.PLAYER_MAX


def _norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def normalize_v3(v3: pd.DataFrame) -> pd.DataFrame:
    out = v3.copy()
    for c in ("gameId", "period", "actionNumber", "actionId", "personId", "teamId"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _v3_action_map(v3_game: pd.DataFrame) -> dict[tuple[int, int], int]:
    if v3_game is None or v3_game.empty:
        return {}
    g = normalize_v3(v3_game)
    required = {"period", "actionNumber", "actionId"}
    if not required.issubset(g.columns):
        return {}
    g = g.dropna(subset=["period", "actionNumber", "actionId"])
    out: dict[tuple[int, int], int] = {}
    for (period, action), rows in g.groupby(["period", "actionNumber"], sort=False):
        out[(int(period), int(action))] = int(rows.actionId.min())
    return out


def _load_evidence_repairs() -> dict[tuple[int, int, int], set[int]]:
    if not EVIDENCE_PATH.exists():
        return {}
    payload = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    out = {}
    for r in payload.get("repairs", []):
        out[(int(r["game_id"]), int(r["period"]), int(r["team_id"]))] = {int(x) for x in r["starters"]}
    return out


def _description(row: pd.Series) -> str:
    return _norm(" ".join(str(row.get(c, "") or "") for c in ("HOMEDESCRIPTION", "NEUTRALDESCRIPTION", "VISITORDESCRIPTION")))


def _ignore_participant_constraint(row: pd.Series) -> bool:
    typ = int(row.EVENTMSGTYPE)
    if typ in {8, 9, 11, 12, 13, 18}:
        return True
    text = _description(row)
    return "technical" in text or "eject" in text


def _team_participants(row: pd.Series, team_id: int) -> set[int]:
    if _ignore_participant_constraint(row):
        return set()
    out = set()
    for n in (1, 2, 3):
        pid = int(row.get(f"PLAYER{n}_ID", 0) or 0)
        ptype = int(row.get(f"PERSON{n}TYPE", 0) or 0)
        raw_team = row.get(f"PLAYER{n}_TEAM_ID")
        tid = int(raw_team) if pd.notna(raw_team) else 0
        if tid == team_id and 0 < pid < PLAYER_MAX and ptype in {4, 5}:
            out.add(pid)
    return out


def _sub_team(row: pd.Series, player_team: dict[int, int], lineups: dict[int, set[int]] | None = None) -> int:
    t1 = int(row.PLAYER1_TEAM_ID) if pd.notna(row.PLAYER1_TEAM_ID) else 0
    t2 = int(row.PLAYER2_TEAM_ID) if pd.notna(row.PLAYER2_TEAM_ID) else 0
    if t1 > 0 and t2 > 0 and t1 != t2:
        raise ValueError(f"cross-team substitution event={int(row.EVENTNUM)} t1={t1} t2={t2}")
    if t1 > 0:
        return t1
    if t2 > 0:
        return t2
    outgoing = int(row.PLAYER1_ID or 0)
    incoming = int(row.PLAYER2_ID or 0)
    if outgoing in player_team:
        return int(player_team[outgoing])
    if incoming in player_team:
        return int(player_team[incoming])
    if lineups:
        for team, players in lineups.items():
            if outgoing in players:
                return int(team)
    return 0


def _simulate_team(period: pd.DataFrame, team_id: int, starters: Iterable[int], player_team: dict[int, int]) -> tuple[bool, list[dict]]:
    lineup = set(int(x) for x in starters)
    violations: list[dict] = []
    ended = False
    for _, row in period.iterrows():
        typ = int(row.EVENTMSGTYPE)
        if typ == 13:
            ended = True
        if typ == 8:
            st = _sub_team(row, player_team)
            if st != team_id:
                continue
            outgoing = int(row.PLAYER1_ID or 0)
            incoming = int(row.PLAYER2_ID or 0)
            if outgoing not in lineup or incoming in lineup:
                return False, [{"kind": "substitution", "event_num": int(row.EVENTNUM), "out": outgoing, "in": incoming, "lineup": sorted(lineup)}]
            lineup.remove(outgoing)
            lineup.add(incoming)
            continue
        if ended:
            continue
        for pid in _team_participants(row, team_id):
            if pid not in lineup:
                violations.append({"kind": "participant", "event_num": int(row.EVENTNUM), "player_id": pid, "event_type": typ})
    return True, violations


def _candidate_starters(period: pd.DataFrame, team_id: int, player_team: dict[int, int]) -> set[int]:
    participants = {p for p, t in player_team.items() if int(t) == int(team_id)}
    subs = period.loc[period.EVENTMSGTYPE.eq(8)]
    team_subs = []
    for _, row in subs.iterrows():
        if _sub_team(row, player_team) == team_id:
            team_subs.append(row)
    sub_out = {int(r.PLAYER1_ID) for r in team_subs if int(r.PLAYER1_ID or 0) > 0}
    sub_in = {int(r.PLAYER2_ID) for r in team_subs if int(r.PLAYER2_ID or 0) > 0}
    candidates = set(participants) - (sub_in - sub_out)
    both = sub_in & sub_out
    for player in both:
        rows = [r for r in team_subs if int(r.PLAYER1_ID or 0) == player or int(r.PLAYER2_ID or 0) == player]
        if not rows:
            continue
        first = rows[0]
        if int(first.PLAYER2_ID or 0) == player:
            candidates.discard(player)
        else:
            candidates.add(player)
    return candidates


def _choose_starters(
    period: pd.DataFrame,
    game_id: int,
    period_number: int,
    team_id: int,
    player_team: dict[int, int],
    prior: set[int] | None,
    evidence_repairs: dict[tuple[int, int, int], set[int]],
) -> tuple[set[int], dict]:
    key = (game_id, period_number, team_id)
    explicit = legacy.core.STARTER_REPAIRS.get(key)
    if explicit is not None:
        chosen = {int(x) for x in explicit}
        return chosen, {"type": "locked_starter_repair", "team_id": team_id, "period": period_number, "starters": sorted(chosen)}
    if key in evidence_repairs:
        chosen = set(evidence_repairs[key])
        return chosen, {"type": "evidence_starter_repair", "team_id": team_id, "period": period_number, "starters": sorted(chosen), "evidence_file": str(EVIDENCE_PATH.relative_to(BASE))}

    candidates = _candidate_starters(period, team_id, player_team)
    pool = set(candidates)
    if prior:
        pool |= set(prior)
    if len(pool) < 5:
        raise ValueError(f"unresolved v3/team-local starters game={game_id} period={period_number} team={team_id}: candidates={sorted(candidates)} prior={sorted(prior or [])}")

    combos = [set(c) for c in combinations(sorted(pool), 5) if candidates.issubset(c)] if len(candidates) < 5 else [set(c) for c in combinations(sorted(candidates), 5)]
    evaluated = []
    for combo in combos:
        legal, violations = _simulate_team(period, team_id, combo, player_team)
        if legal:
            evaluated.append((len(violations), tuple(sorted(combo)), violations))
    if not evaluated:
        raise ValueError(f"no legal v3/team-local starter solution game={game_id} period={period_number} team={team_id}: candidates={sorted(candidates)} prior={sorted(prior or [])}")
    evaluated.sort(key=lambda x: (x[0], x[1]))
    best_score = evaluated[0][0]
    best = [x for x in evaluated if x[0] == best_score]
    if len(best) != 1:
        raise ValueError(f"non-unique v3/team-local starter solution game={game_id} period={period_number} team={team_id}: candidates={sorted(candidates)} prior={sorted(prior or [])} best_score={best_score} solutions={[list(x[1]) for x in best[:10]]}")
    score, chosen_tuple, violations = best[0]
    if score:
        raise ValueError(f"starter solution requires missing in-period lineup transition game={game_id} period={period_number} team={team_id}: starters={list(chosen_tuple)} violations={violations[:10]}")
    chosen = set(chosen_tuple)
    return chosen, {"type": "v3_team_local_starter_solution", "team_id": team_id, "period": period_number, "candidates": sorted(candidates), "prior": sorted(prior or []), "starters": sorted(chosen)}


def reconstruct_game_lineups(game: pd.DataFrame, v3_game: pd.DataFrame) -> core.GameLineups:
    prepared, base_repairs = legacy.prepare_nba_game(game)
    if prepared.empty:
        raise ValueError("empty historical NBA game")
    prepared = prepared.copy()
    prepared["DESCRIPTION_NORM"] = core.nba_description(prepared)
    prepared["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(prepared.PERIOD, prepared.PCTIMESTRING)]
    order_map = _v3_action_map(v3_game)
    prepared["V3_ORDER"] = [order_map.get((int(p), int(ev)), 10_000_000 + int(ev)) for p, ev in zip(prepared.PERIOD, prepared.EVENTNUM)]

    game_id = int(prepared.GAME_ID.iloc[0])
    player_team = core._player_team(prepared)
    teams = sorted(set(int(x) for x in player_team.values()))
    if len(teams) != 2:
        raise ValueError(f"expected two teams game={game_id}, got {teams}")
    evidence_repairs = _load_evidence_repairs()

    seconds: dict[int, int] = {}
    snapshots = []
    repairs = list(base_repairs)
    prior_by_team: dict[int, set[int]] | None = None

    for period_number, period in prepared.groupby("PERIOD", sort=True):
        period_number = int(period_number)
        period = period.sort_values(["ELAPSED", "V3_ORDER", "EVENTNUM"], kind="stable").copy()
        lineups: dict[int, set[int]] = {}
        for team_id in teams:
            prior = None if prior_by_team is None else prior_by_team.get(team_id)
            starters, audit = _choose_starters(period, game_id, period_number, team_id, player_team, prior, evidence_repairs)
            if len(starters) != 5:
                raise ValueError(f"invalid starters game={game_id} period={period_number} team={team_id}: {sorted(starters)}")
            lineups[team_id] = set(starters)
            repairs.append({"game_id": game_id, **audit})
        if len(set().union(*lineups.values())) != 10:
            raise ValueError(f"invalid ten-player opening state game={game_id} period={period_number}")

        period_start = (period_number - 1) * 720 if period_number <= 4 else 2880 + (period_number - 5) * 300
        last_time = period_start
        rows = []
        for _, event in period.iterrows():
            now = int(event.ELAPSED)
            if now > last_time:
                for players in lineups.values():
                    for player in players:
                        seconds[player] = seconds.get(player, 0) + now - last_time
                last_time = now
            if int(event.EVENTMSGTYPE) == 8:
                team_id = _sub_team(event, player_team, lineups)
                if team_id not in lineups:
                    raise ValueError(f"cannot resolve substitution team game={game_id} event={int(event.EVENTNUM)}")
                outgoing, incoming = int(event.PLAYER1_ID or 0), int(event.PLAYER2_ID or 0)
                if outgoing not in lineups[team_id]:
                    raise ValueError(f"substitution outgoing player absent game={game_id} event={int(event.EVENTNUM)}: {outgoing}")
                if incoming in lineups[team_id]:
                    raise ValueError(f"substitution incoming player already present game={game_id} event={int(event.EVENTNUM)}: {incoming}")
                lineups[team_id].remove(outgoing)
                lineups[team_id].add(incoming)
            if any(len(players) != 5 for players in lineups.values()) or len(set().union(*lineups.values())) != 10:
                raise ValueError(f"invalid team-local lineup game={game_id} event={int(event.EVENTNUM)}")
            item = event.to_dict()
            item["LINEUP"] = tuple(sorted(set().union(*lineups.values())))
            rows.append(item)
        period_end = period_start + (720 if period_number <= 4 else 300)
        if period_end > last_time:
            for players in lineups.values():
                for player in players:
                    seconds[player] = seconds.get(player, 0) + period_end - last_time
        snapshots.append(pd.DataFrame(rows))
        prior_by_team = {team: set(players) for team, players in lineups.items()}

    result = core.GameLineups(pd.concat(snapshots, ignore_index=True), seconds, repairs)

    for (repair_game, period_number), spec in legacy.PERIOD_START_GAP_REPAIRS.items():
        if game_id != repair_game:
            continue
        period_events = result.events[result.events.PERIOD.eq(period_number)]
        source = period_events[period_events.EVENTNUM.eq(int(spec["start_event"]))]
        if len(source) != 1 or int(source.iloc[0].EVENTMSGTYPE) != 12 or str(source.iloc[0].PCTIMESTRING) != str(spec["start_clock"]):
            raise ValueError(f"locked period-start repair source changed game={game_id} period={period_number}")
        gap = int(spec["seconds_removed"])
        first_elapsed = int(period_events.ELAPSED.min())
        first = period_events[period_events.ELAPSED.eq(first_elapsed)].sort_values(["V3_ORDER", "EVENTNUM"], kind="stable").iloc[0]
        players = [int(p) for p in first.LINEUP]
        for player in players:
            if result.seconds.get(player, 0) < gap:
                raise ValueError(f"locked period-start repair would make player {player} seconds negative")
            result.seconds[player] -= gap
        result.repairs.append({"game_id": game_id, "period": period_number, "type": "period_start_clock_gap_repair", "source_event": int(spec["start_event"]), "source_clock": str(spec["start_clock"]), "seconds_removed": gap, "players": sorted(players)})
    return result


join_pbp_rebounds = legacy.join_pbp_rebounds
classify_rebounds = legacy.classify_rebounds
