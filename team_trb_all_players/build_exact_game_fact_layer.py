#!/usr/bin/env python3
"""Build an exact, reusable per-game TREB fact layer from local historical feeds.

This deliberately operates at game granularity rather than tenure granularity.
Once a season is reconstructed, roster/tenure corrections can be applied by
selecting the appropriate team games without replaying historical PBP.

PBP Stats defines the rebound universe. NBA Stats + NBA Stats v3 provide exact
lineup timing/chronology through the already validated production engines.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core
import production_treb_engine as rebound_engine
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io


def _duration_seconds(game: pd.DataFrame) -> int:
    max_period = int(pd.to_numeric(game["PERIOD"], errors="coerce").max())
    return 2880 + max(0, max_period - 4) * 300


def _player_names(game: pd.DataFrame) -> dict[int, str]:
    evidence: dict[int, list[str]] = {}
    for n in (1, 2, 3):
        id_col = f"PLAYER{n}_ID"
        name_col = f"PLAYER{n}_NAME"
        if id_col not in game.columns or name_col not in game.columns:
            continue
        ids = pd.to_numeric(game[id_col], errors="coerce")
        for pid, name in zip(ids, game[name_col]):
            if pd.notna(pid) and 0 < int(pid) < core.PLAYER_MAX and pd.notna(name) and str(name).strip():
                evidence.setdefault(int(pid), []).append(str(name).strip())
    return {pid: max(set(names), key=names.count) for pid, names in evidence.items() if names}


def _team_abbreviations(game: pd.DataFrame) -> dict[int, str]:
    evidence: dict[int, list[str]] = {}
    for n in (1, 2, 3):
        team_col = f"PLAYER{n}_TEAM_ID"
        abbr_col = f"PLAYER{n}_TEAM_ABBREVIATION"
        if team_col not in game.columns or abbr_col not in game.columns:
            continue
        teams = pd.to_numeric(game[team_col], errors="coerce")
        for tid, abbr in zip(teams, game[abbr_col]):
            if pd.notna(tid) and int(tid) > 0 and pd.notna(abbr) and str(abbr).strip():
                evidence.setdefault(int(tid), []).append(str(abbr).strip())
    return {tid: max(set(values), key=values.count) for tid, values in evidence.items() if values}


def _count(mask: pd.Series) -> int:
    return int(mask.fillna(False).sum())


def build_game(game_id: int, nba_game: pd.DataFrame, v3_game: pd.DataFrame, pbp_game: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    lineups = lineup_engine.reconstruct_game_lineups(nba_game, v3_game)
    joined, join_audit = rebound_engine.join_pbp_rebounds(lineups, pbp_game)
    if int(join_audit.get("unmatched_rebound_bearing_rows", 0)) != 0:
        raise ValueError(
            f"unmatched PBP rebound rows game={game_id}: "
            f"{join_audit.get('unmatched_rebound_bearing_rows')}"
        )
    joined = rebound_engine.classify_rebounds(joined)

    player_team = core._player_team(nba_game)
    team_abbr = _team_abbreviations(nba_game)
    names = _player_names(nba_game)
    teams = sorted(set(int(x) for x in player_team.values()))
    if len(teams) != 2:
        raise ValueError(f"expected two teams game={game_id}, got {teams}")
    missing_abbr = [tid for tid in teams if tid not in team_abbr]
    if missing_abbr:
        raise ValueError(f"missing team abbreviation game={game_id}: {missing_abbr}")

    duration = _duration_seconds(nba_game)
    date = None
    if "GAMEDATE" in pbp_game.columns and not pbp_game.empty:
        value = pbp_game.GAMEDATE.iloc[0]
        date = None if pd.isna(value) else str(value)

    real = joined.IS_REAL_REBOUND.astype(bool)
    oreb = joined.IS_OREB.astype(bool)

    team_rows: list[dict] = []
    for tid in teams:
        abbr = team_abbr[tid]
        team_offense = ~joined.OPPONENT.astype(str).eq(abbr)
        team_defense = joined.OPPONENT.astype(str).eq(abbr)
        team_rows.append({
            "game_id": int(game_id),
            "game_date": date,
            "team_id": int(tid),
            "team_abbr": abbr,
            "game_seconds": int(duration),
            "team_oreb": _count(team_offense & oreb),
            "team_dreb": _count(team_defense & real & ~oreb),
            "opponent_oreb": _count(team_defense & oreb),
            "opponent_dreb": _count(team_offense & real & ~oreb),
        })

    player_rows: list[dict] = []
    for pid, seconds in sorted(lineups.seconds.items()):
        pid = int(pid)
        sec = int(round(float(seconds)))
        if sec <= 0:
            continue
        tid = player_team.get(pid)
        if tid is None:
            raise ValueError(f"positive-second player has no team mapping game={game_id} player={pid}")
        tid = int(tid)
        abbr = team_abbr[tid]
        on = joined.LINEUP.map(lambda lineup: pid in lineup)
        team_offense = ~joined.OPPONENT.astype(str).eq(abbr)
        team_defense = joined.OPPONENT.astype(str).eq(abbr)
        player_rows.append({
            "game_id": int(game_id),
            "game_date": date,
            "team_id": tid,
            "team_abbr": abbr,
            "player_id": pid,
            "player": names.get(pid, ""),
            "seconds_on": sec,
            "team_oreb_on": _count(on & team_offense & oreb),
            "team_dreb_on": _count(on & team_defense & real & ~oreb),
            "opponent_oreb_on": _count(on & team_defense & oreb),
            "opponent_dreb_on": _count(on & team_offense & real & ~oreb),
        })

    # Strong game-level invariants. Each team must account for five player-slots.
    for tid in teams:
        observed = sum(r["seconds_on"] for r in player_rows if r["team_id"] == tid)
        expected = duration * 5
        if observed != expected:
            raise ValueError(
                f"team player-seconds mismatch game={game_id} team={tid}: "
                f"observed={observed} expected={expected}"
            )

    audit = {
        "game_id": int(game_id),
        "game_date": date,
        "teams": teams,
        "duration_seconds": duration,
        "players_with_seconds": len(player_rows),
        "lineup_repairs": lineups.repairs,
        "join_audit": join_audit,
    }
    return team_rows, player_rows, audit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))

    nba_ids = set(pd.to_numeric(nba.GAME_ID, errors="coerce").dropna().astype(int))
    v3_ids = set(pd.to_numeric(v3.gameId, errors="coerce").dropna().astype(int))
    pbp_ids = set(pd.to_numeric(pbp.GAMEID, errors="coerce").dropna().astype(int))
    all_ids = sorted(nba_ids & v3_ids & pbp_ids)

    team_rows: list[dict] = []
    player_rows: list[dict] = []
    audits: list[dict] = []
    failures: list[dict] = []

    for i, gid in enumerate(all_ids, 1):
        try:
            tr, pr, audit = build_game(
                gid,
                nba[nba.GAME_ID.eq(gid)].copy(),
                v3[v3.gameId.eq(gid)].copy(),
                pbp[pbp.GAMEID.eq(gid)].copy(),
            )
            team_rows.extend(tr)
            player_rows.extend(pr)
            audits.append(audit)
        except Exception as exc:
            failures.append({"game_id": int(gid), "error": f"{type(exc).__name__}: {exc}"})
        if i % 50 == 0 or i == len(all_ids):
            print(
                f"GAME_FACT_PROGRESS season={args.season} processed={i}/{len(all_ids)} "
                f"success={len(audits)} failures={len(failures)}",
                flush=True,
            )

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    team_df.to_csv(args.output_dir / "team_game_treb.csv.gz", index=False, compression="gzip")
    player_df.to_csv(args.output_dir / "player_game_treb_on.csv.gz", index=False, compression="gzip")
    (args.output_dir / "game_audit.json").write_text(json.dumps(audits, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")

    qa = {
        "season": args.season,
        "nba_games": len(nba_ids),
        "v3_games": len(v3_ids),
        "pbp_games": len(pbp_ids),
        "common_games": len(all_ids),
        "successful_games": len(audits),
        "failed_games": len(failures),
        "team_game_rows": int(len(team_df)),
        "player_game_rows": int(len(player_df)),
        "status": "PASS" if not failures and nba_ids == v3_ids == pbp_ids else "INCOMPLETE",
    }
    (args.output_dir / "qa.json").write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(qa, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
