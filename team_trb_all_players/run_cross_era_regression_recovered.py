#!/usr/bin/env python3
"""Cross-era regression gate for the recovered TREB production engine.

Structural checks are strict. Rebound-count tolerances apply only when comparing
reconstructed counts with the retained historical core; they never alter output.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from production_treb_engine import classify_rebounds, join_pbp_rebounds, reconstruct_game_lineups

SAMPLES = {
    2000: ("ATL", 1610612737),
    2004: ("BOS", 1610612738),
    2008: ("CLE", 1610612739),
    2012: ("SAS", 1610612759),
    2016: ("OKC", 1610612760),
    2020: ("PHX", 1610612756),
    2024: ("DEN", 1610612743),
}


def json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def normalize_nba(df: pd.DataFrame) -> pd.DataFrame:
    numeric = ["GAME_ID", "EVENTNUM", "EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PERIOD",
               "PLAYER1_ID", "PLAYER2_ID", "PLAYER3_ID", "PLAYER1_TEAM_ID", "PLAYER2_TEAM_ID", "PLAYER3_TEAM_ID",
               "PERSON1TYPE", "PERSON2TYPE", "PERSON3TYPE"]
    for c in numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    return df


def normalize_pbp(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("GAMEID", "PERIOD", "OFFENSIVEREBOUNDS"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    return df


def game_has_team(game: pd.DataFrame, team_id: int) -> bool:
    for c in ("PLAYER1_TEAM_ID", "PLAYER2_TEAM_ID", "PLAYER3_TEAM_ID"):
        if c in game.columns and game[c].eq(team_id).any():
            return True
    return False


def reconstruct_team(nba: pd.DataFrame, pbp: pd.DataFrame, team_abbr: str, team_id: int,
                     player_ids: set[int]) -> tuple[dict[int, dict[str, int]], dict]:
    totals = {pid: {"seconds": 0, "team_off_rebounds": 0, "team_def_rebounds": 0, "team_rebounds": 0}
              for pid in player_ids}
    audit = {"games_expected": 0, "games_completed": 0, "rebound_bearing_rows": 0,
             "matched_rebound_bearing_rows": 0, "unmatched_rebound_bearing_rows": 0,
             "manual_join_repairs": 0, "exceptions": [], "repairs": []}
    pbp_groups = {int(g): x for g, x in pbp.groupby("GAMEID", sort=False)}
    for gid, nba_game in nba.groupby("GAME_ID", sort=False):
        gid = int(gid)
        if not game_has_team(nba_game, team_id):
            continue
        audit["games_expected"] += 1
        try:
            pbp_game = pbp_groups.get(gid)
            if pbp_game is None:
                raise ValueError("PBP Stats game missing")
            lineups = reconstruct_game_lineups(nba_game)
            joined, game_audit = join_pbp_rebounds(lineups, pbp_game)
            audit["rebound_bearing_rows"] += int(game_audit["rebound_bearing_rows"])
            audit["matched_rebound_bearing_rows"] += int(game_audit["matched_rebound_bearing_rows"])
            audit["unmatched_rebound_bearing_rows"] += int(game_audit["unmatched_rebound_bearing_rows"])
            audit["manual_join_repairs"] += int(game_audit.get("manual_join_repairs", 0))
            audit["repairs"].extend(lineups.repairs)
            if game_audit["unmatched_rebound_bearing_rows"]:
                raise ValueError(f"unmatched rebound-bearing rows={game_audit['unmatched_rebound_bearing_rows']}")
            joined = classify_rebounds(joined)
            if not joined.empty:
                team_offense = ~joined.OPPONENT.astype(str).eq(team_abbr)
                team_defense = ~team_offense
                oreb = joined.IS_OREB.astype(bool)
                dreb = joined.IS_REAL_REBOUND.astype(bool) & ~oreb
            for pid in player_ids:
                totals[pid]["seconds"] += int(lineups.seconds.get(pid, 0))
                if not joined.empty:
                    on = joined.LINEUP.map(lambda x, p=pid: p in x)
                    totals[pid]["team_off_rebounds"] += int((on & team_offense & oreb).sum())
                    totals[pid]["team_def_rebounds"] += int((on & team_defense & dreb).sum())
            audit["games_completed"] += 1
        except Exception as exc:
            audit["exceptions"].append({"game_id": gid, "error": str(exc)})
    for pid in player_ids:
        totals[pid]["team_rebounds"] = totals[pid]["team_off_rebounds"] + totals[pid]["team_def_rebounds"]
    return totals, audit


def compare_player(player_id: int, player: str, expected: dict, actual: dict) -> dict:
    sec_diff = actual["seconds"] - expected["seconds"]
    o_diff = actual["team_off_rebounds"] - expected["team_off_rebounds"]
    d_diff = actual["team_def_rebounds"] - expected["team_def_rebounds"]
    t_diff = actual["team_rebounds"] - expected["team_rebounds"]
    passed = (sec_diff == 0 and abs(o_diff) <= 5 and abs(d_diff) <= 5 and abs(t_diff) <= 5
              and abs(o_diff) + abs(d_diff) <= 8)
    return {
        "player_id": player_id, "player": player,
        "expected_seconds": expected["seconds"], "actual_seconds": actual["seconds"], "seconds_diff": sec_diff,
        "expected_oreb": expected["team_off_rebounds"], "actual_oreb": actual["team_off_rebounds"],
        "oreb_diff": o_diff, "oreb_abs_diff": abs(o_diff),
        "expected_dreb": expected["team_def_rebounds"], "actual_dreb": actual["team_def_rebounds"],
        "dreb_diff": d_diff, "dreb_abs_diff": abs(d_diff),
        "expected_team_rebounds": expected["team_rebounds"], "actual_team_rebounds": actual["team_rebounds"],
        "team_rebounds_diff": t_diff, "team_rebounds_abs_diff": abs(t_diff),
        "combined_abs_oreb_dreb_diff": abs(o_diff) + abs(d_diff), "passed": bool(passed),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent
    parser.add_argument("--raw", type=Path, default=root / "impact_database" / "local_raw")
    parser.add_argument("--core", type=Path, default=root / "impact_database" / "outputs")
    parser.add_argument("--output", type=Path, default=root / "impact_database" / "cross_era_regression_recovered.json")
    parser.add_argument("--years", nargs="*", type=int, choices=sorted(SAMPLES), default=sorted(SAMPLES))
    args = parser.parse_args()
    seasons = []
    for year in args.years:
        abbr, team_id = SAMPLES[year]
        season = f"{year}-{str(year + 1)[-2:]}"
        core = pd.read_csv(args.core / season / "team_rebound_derived.csv.gz")
        candidates = core[(core.team_id.eq(team_id)) & core.seconds.notna()].nlargest(3, "seconds")
        expected = {}
        names = {}
        for _, row in candidates.iterrows():
            pid = int(row.player_id)
            expected[pid] = {"seconds": int(row.seconds), "team_off_rebounds": int(row.team_off_rebounds),
                             "team_def_rebounds": int(row.team_def_rebounds), "team_rebounds": int(row.team_rebounds)}
            names[pid] = str(row.player)
        nba = normalize_nba(pd.read_csv(args.raw / f"nbastats_{year}.csv", low_memory=False))
        pbp = normalize_pbp(pd.read_csv(args.raw / f"pbpstats_{year}.csv", low_memory=False))
        actual, audit = reconstruct_team(nba, pbp, abbr, team_id, set(expected))
        comparisons = [compare_player(pid, names[pid], expected[pid], actual[pid]) for pid in expected]
        structural = (audit["games_completed"] == audit["games_expected"]
                      and audit["unmatched_rebound_bearing_rows"] == 0 and not audit["exceptions"])
        passed = structural and all(x["passed"] for x in comparisons)
        result = {"season": season, "team": abbr, "team_id": team_id, "passed": bool(passed),
                  "comparisons": comparisons, "join_and_lineup_audit": audit}
        seasons.append(result)
        checkpoint = args.output.with_name(f"cross_era_recovered_{year}_checkpoint.json")
        checkpoint.write_text(json.dumps(result, indent=2, sort_keys=True, default=json_default) + "\n")
        print(json.dumps({"season": season, "passed": passed, "games": audit["games_completed"],
                          "expected_games": audit["games_expected"], "exceptions": len(audit["exceptions"]),
                          "unmatched": audit["unmatched_rebound_bearing_rows"]}), flush=True)
        for x in comparisons:
            print(" ", x["player"], "sec", x["seconds_diff"], "OREB", x["oreb_diff"],
                  "DREB", x["dreb_diff"], "TRB", x["team_rebounds_diff"], x["passed"], flush=True)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "status": "PASS" if all(s["passed"] for s in seasons) else "FAIL", "seasons": seasons}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, default=json_default) + "\n")
    print(report["status"], flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
