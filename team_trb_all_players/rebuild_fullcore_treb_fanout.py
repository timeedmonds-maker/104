#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

import build_exact_game_fact_layer as base
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

KEYS = ["season", "team_id", "player_id"]
COUNT_ON = ["team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on"]
TEAM_COUNTS = ["team_oreb", "team_dreb", "opponent_oreb", "opponent_dreb"]


def sid(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def load_targets(path: Path, season: str) -> list[dict]:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if str(r.get("season")) == season and bool(r.get("full_core_reuse")):
                r["player_id"] = sid(r["player_id"])
                r["team_id"] = int(r["team_id"])
                rows.append(r)
    return rows


def nba_game_teams(game: pd.DataFrame) -> set[int]:
    out: set[int] = set()
    for n in (1, 2, 3):
        c = f"PLAYER{n}_TEAM_ID"
        if c not in game.columns:
            continue
        vals = pd.to_numeric(game[c], errors="coerce").dropna()
        out.update(int(x) for x in vals if int(x) > 0)
    return out


def ratio(team_reb: float, opp_reb: float) -> float:
    den = float(team_reb) + float(opp_reb)
    if den <= 0:
        raise ValueError(f"nonpositive rebound denominator team={team_reb} opp={opp_reb}")
    return float(team_reb) / den


def write_progress(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def checkpoint_facts(outdir: Path, team_rows: list[dict], player_rows: list[dict], failures: list[dict]) -> None:
    pd.DataFrame(team_rows).to_csv(outdir / "team_game_treb.partial.csv.gz", index=False, compression="gzip")
    pd.DataFrame(player_rows).to_csv(outdir / "player_game_treb_on.partial.csv.gz", index=False, compression="gzip")
    (outdir / "game_failures.partial.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    season = f"{args.year}-{(args.year + 1) % 100:02d}"
    outdir = args.output_dir
    outdir.mkdir(parents=True, exist_ok=True)
    progress = outdir / "progress.json"

    targets = load_targets(args.targets, season)
    if not targets:
        raise RuntimeError(f"No full_core_reuse targets for {season}")
    target_keys = {(season, int(r["team_id"]), sid(r["player_id"])) for r in targets}
    if len(target_keys) != len(targets):
        raise RuntimeError(f"Duplicate full-core target keys in {season}")

    write_progress(progress, {
        "phase": "LOAD_SOURCES", "season": season, "required_keys": len(targets),
        "processed_games": 0, "successful_games": 0, "failed_games": 0,
    })

    nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))

    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}
    nids, vids, pids = set(ng), set(vg), set(pg)
    common = sorted(nids & vids & pids)
    source_gap_ids = sorted((nids | vids | pids) - (nids & vids & pids))

    team_rows: list[dict] = []
    player_rows: list[dict] = []
    audits: list[dict] = []
    failures: list[dict] = []
    bad_teams: set[int] = set()

    for gid in source_gap_ids:
        teams = nba_game_teams(ng[gid]) if gid in ng else set()
        bad_teams.update(teams)
        failures.append({"game_id": gid, "status": "SOURCE_SET_GAP", "teams": sorted(teams)})

    write_progress(progress, {
        "phase": "RECONSTRUCT_GAMES", "season": season, "required_keys": len(targets),
        "nba_games": len(nids), "v3_games": len(vids), "pbp_games": len(pids),
        "common_games": len(common), "source_gap_games": len(source_gap_ids),
        "processed_games": 0, "successful_games": 0, "failed_games": len(failures),
    })

    for i, gid in enumerate(common, 1):
        try:
            tr, pr, audit = base.build_game(gid, ng[gid], vg[gid], pg[gid])
            team_rows.extend(tr)
            player_rows.extend(pr)
            audits.append(audit)
        except Exception as exc:
            teams = nba_game_teams(ng[gid])
            bad_teams.update(teams)
            failures.append({
                "game_id": gid, "status": "RECONSTRUCTION_FAIL", "teams": sorted(teams),
                "error": f"{type(exc).__name__}: {exc}",
            })
        if i % 25 == 0 or i == len(common):
            payload = {
                "phase": "RECONSTRUCT_GAMES", "season": season, "required_keys": len(targets),
                "processed_games": i, "common_games": len(common), "successful_games": len(audits),
                "failed_games": len(failures), "bad_teams": len(bad_teams),
                "team_fact_rows": len(team_rows), "player_fact_rows": len(player_rows),
            }
            write_progress(progress, payload)
            checkpoint_facts(outdir, team_rows, player_rows, failures)
            print(json.dumps({"event": "GAME_CHECKPOINT", **payload}), flush=True)

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    if team_df.empty or player_df.empty:
        raise RuntimeError(f"No reconstructed facts for {season}")

    team_df.to_csv(outdir / "team_game_treb.csv.gz", index=False, compression="gzip")
    player_df.to_csv(outdir / "player_game_treb_on.csv.gz", index=False, compression="gzip")
    (outdir / "game_audit.json").write_text(json.dumps(audits, indent=2) + "\n", encoding="utf-8")
    (outdir / "game_failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")

    write_progress(progress, {
        "phase": "AGGREGATE_FULLCORE", "season": season, "required_keys": len(targets),
        "successful_games": len(audits), "failed_games": len(failures), "bad_teams": len(bad_teams),
    })

    tg = team_df.groupby("team_id", as_index=False).agg(
        game_seconds=("game_seconds", "sum"),
        team_oreb=("team_oreb", "sum"), team_dreb=("team_dreb", "sum"),
        opponent_oreb=("opponent_oreb", "sum"), opponent_dreb=("opponent_dreb", "sum"),
        reconstructed_team_games=("game_id", "nunique"),
    )
    pgagg = player_df.groupby(["team_id", "player_id"], as_index=False).agg(
        seconds_on=("seconds_on", "sum"),
        team_oreb_on=("team_oreb_on", "sum"), team_dreb_on=("team_dreb_on", "sum"),
        opponent_oreb_on=("opponent_oreb_on", "sum"), opponent_dreb_on=("opponent_dreb_on", "sum"),
        reconstructed_player_games=("game_id", "nunique"),
    )
    team_map = {int(r.team_id): r for r in tg.itertuples(index=False)}
    player_map = {(int(r.team_id), sid(r.player_id)): r for r in pgagg.itertuples(index=False)}

    out: list[dict] = []
    for t in targets:
        tid, pid = int(t["team_id"]), sid(t["player_id"])
        row = {
            "season": season, "team_id": tid, "player_id": pid, "player": t.get("player", ""),
            "target_minutes_on": float(t.get("seconds_on", 0.0)) / 60.0,
            "source": "fresh_exact_game_facts_shufinskiy_nba_v3_pbpstats",
            "rounded_percentage_backsolve_used": False,
            "opponent_rebound_inference_used": False,
            "whole_team_subtraction_across_partial_tenure_used": False,
        }
        if tid in bad_teams:
            row.update(status="TEAM_GAME_GAP", error="At least one team game had source/reconstruction failure")
            out.append(row); continue
        team = team_map.get(tid)
        player = player_map.get((tid, pid))
        if team is None:
            row.update(status="MISSING_TEAM_TOTAL", error="No reconstructed team total")
            out.append(row); continue
        if player is None:
            row.update(status="MISSING_PLAYER_TOTAL", error="No reconstructed positive-second player row")
            out.append(row); continue
        try:
            target_seconds = float(t.get("seconds_on", 0.0))
            actual_seconds = float(player.seconds_on)
            if abs(actual_seconds - target_seconds) > 60.0:
                raise ValueError(f"minutes mismatch actual={actual_seconds/60:.6f} target={target_seconds/60:.6f}")

            team_on = float(player.team_oreb_on + player.team_dreb_on)
            opp_on = float(player.opponent_oreb_on + player.opponent_dreb_on)
            team_total = float(team.team_oreb + team.team_dreb)
            opp_total = float(team.opponent_oreb + team.opponent_dreb)
            team_off = team_total - team_on
            opp_off = opp_total - opp_on
            if min(team_off, opp_off) < 0:
                raise ValueError(f"negative off count team_off={team_off} opp_off={opp_off}")
            treb_on = ratio(team_on, opp_on)
            treb_off = ratio(team_off, opp_off)
            if not (0.25 <= treb_on <= 0.75 and 0.25 <= treb_off <= 0.75):
                raise ValueError(f"implausible TREB on={treb_on} off={treb_off}")
            seconds_off = float(team.game_seconds) - actual_seconds
            if seconds_off < 0:
                raise ValueError(f"negative seconds_off={seconds_off}")

            row.update({
                "direct_treb_on": treb_on, "direct_treb_off": treb_off,
                "direct_minutes_on": actual_seconds / 60.0,
                "direct_minutes_off": seconds_off / 60.0,
                "team_oreb_on": int(player.team_oreb_on), "team_dreb_on": int(player.team_dreb_on),
                "opponent_oreb_on": int(player.opponent_oreb_on), "opponent_dreb_on": int(player.opponent_dreb_on),
                "team_oreb_off": int(team.team_oreb - player.team_oreb_on),
                "team_dreb_off": int(team.team_dreb - player.team_dreb_on),
                "opponent_oreb_off": int(team.opponent_oreb - player.opponent_oreb_on),
                "opponent_dreb_off": int(team.opponent_dreb - player.opponent_dreb_on),
                "reconstructed_team_games": int(team.reconstructed_team_games),
                "reconstructed_player_games": int(player.reconstructed_player_games),
                "status": "PASS", "error": "",
            })
        except Exception as exc:
            row.update(status="INVALID_EXACT_ROW", error=f"{type(exc).__name__}: {exc}")
        out.append(row)

    result = pd.DataFrame(out)
    result["player_id"] = result.player_id.astype(str)
    result.to_csv(outdir / f"fresh_fullcore_{args.year}.csv", index=False)
    pass_rows = int(result.status.astype(str).eq("PASS").sum())
    fail_rows = int(len(result) - pass_rows)
    qa = {
        "status": "PASS" if fail_rows == 0 else "INCOMPLETE",
        "season": season, "required_full_core_keys": len(targets),
        "output_rows": len(result), "pass_rows": pass_rows, "failure_rows": fail_rows,
        "nba_games": len(nids), "v3_games": len(vids), "pbp_games": len(pids),
        "common_games": len(common), "successful_games": len(audits),
        "source_or_reconstruction_failures": len(failures), "bad_teams": sorted(bad_teams),
        "rounded_percentage_backsolve_used": False,
        "opponent_rebound_inference_used": False,
        "whole_team_subtraction_across_partial_tenure_used": False,
        "full_core_only": True,
    }
    (outdir / "season_gate.json").write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    write_progress(progress, {"phase": "COMPLETE", **qa})
    print(json.dumps(qa, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
