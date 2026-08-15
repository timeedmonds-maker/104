#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import build_exact_game_fact_layer as base
import production_treb_engine_v3 as lineup_engine
import rebuild_fullcore_treb_fanout as core
import run_local_treb_production as io


def _read_resume(resume_dir: Path, season: str, common: list[int], source_gap_ids: list[int]) -> tuple[list[dict], list[dict], list[dict], set[int], int, int]:
    progress_path = resume_dir / "progress.json"
    team_path = resume_dir / "team_game_treb.partial.csv.gz"
    player_path = resume_dir / "player_game_treb_on.partial.csv.gz"
    failures_path = resume_dir / "game_failures.partial.json"
    missing = [str(p) for p in (progress_path, team_path, player_path, failures_path) if not p.is_file()]
    if missing:
        raise RuntimeError(f"Resume checkpoint missing required files: {missing}")

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if str(progress.get("season")) != season:
        raise RuntimeError(f"Resume season mismatch checkpoint={progress.get('season')} expected={season}")
    processed = int(progress.get("processed_games", -1))
    if processed < 0 or processed > len(common):
        raise RuntimeError(f"Invalid processed_games={processed} for common_games={len(common)}")
    checkpoint_common = progress.get("common_games")
    if checkpoint_common is not None and int(checkpoint_common) != len(common):
        raise RuntimeError(
            f"Common-game universe changed checkpoint={checkpoint_common} current={len(common)}"
        )

    team_df = pd.read_csv(team_path, low_memory=False)
    player_df = pd.read_csv(player_path, low_memory=False)
    failures = json.loads(failures_path.read_text(encoding="utf-8"))
    if not isinstance(failures, list):
        raise RuntimeError("game_failures.partial.json is not a list")

    if processed and team_df.empty:
        raise RuntimeError("Resume checkpoint has processed games but no team facts")

    successful_ids: set[int] = set()
    if len(team_df):
        if "game_id" not in team_df.columns:
            raise RuntimeError("Resume team facts missing game_id")
        successful_ids = set(pd.to_numeric(team_df["game_id"], errors="raise").astype("int64").tolist())
        counts = team_df.assign(
            game_id=pd.to_numeric(team_df["game_id"], errors="raise").astype("int64")
        ).groupby("game_id").size()
        bad_counts = counts[counts.ne(2)]
        if len(bad_counts):
            raise RuntimeError(f"Resume team facts have non-two-team games: {bad_counts.to_dict()}")

    reconstruction_fail_ids = {
        int(r["game_id"])
        for r in failures
        if str(r.get("status")) == "RECONSTRUCTION_FAIL" and r.get("game_id") is not None
    }
    observed_processed = successful_ids | reconstruction_fail_ids
    expected_processed = set(common[:processed])
    if observed_processed != expected_processed:
        missing_ids = sorted(expected_processed - observed_processed)[:20]
        extra_ids = sorted(observed_processed - expected_processed)[:20]
        raise RuntimeError(
            "Resume checkpoint does not match deterministic processed prefix "
            f"missing={missing_ids} extra={extra_ids}"
        )

    checkpoint_gap_ids = {
        int(r["game_id"])
        for r in failures
        if str(r.get("status")) == "SOURCE_SET_GAP" and r.get("game_id") is not None
    }
    current_gap_ids = set(source_gap_ids)
    if checkpoint_gap_ids != current_gap_ids:
        raise RuntimeError(
            "Source-gap universe changed "
            f"checkpoint_only={sorted(checkpoint_gap_ids-current_gap_ids)[:20]} "
            f"current_only={sorted(current_gap_ids-checkpoint_gap_ids)[:20]}"
        )

    bad_teams: set[int] = set()
    for r in failures:
        for t in r.get("teams", []) or []:
            try:
                bad_teams.add(int(t))
            except Exception:
                pass

    return (
        team_df.to_dict("records"),
        player_df.to_dict("records"),
        failures,
        bad_teams,
        processed,
        len(successful_ids),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--resume-dir", type=Path, required=True)
    ap.add_argument("--resume-run-id", default="")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    season = f"{args.year}-{(args.year + 1) % 100:02d}"
    outdir = args.output_dir
    outdir.mkdir(parents=True, exist_ok=True)
    progress_path = outdir / "progress.json"

    targets = core.load_targets(args.targets, season)
    if not targets:
        raise RuntimeError(f"No full_core_reuse targets for {season}")
    target_keys = {(season, int(r["team_id"]), core.sid(r["player_id"])) for r in targets}
    if len(target_keys) != len(targets):
        raise RuntimeError(f"Duplicate full-core target keys in {season}")

    core.write_progress(
        progress_path,
        {
            "phase": "LOAD_SOURCES",
            "season": season,
            "required_keys": len(targets),
            "processed_games": 0,
            "successful_games": 0,
            "failed_games": 0,
            "resume": True,
        },
    )

    nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))

    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}
    nids, vids, pids = set(ng), set(vg), set(pg)
    common = sorted(nids & vids & pids)
    source_gap_ids = sorted((nids | vids | pids) - (nids & vids & pids))

    (
        team_rows,
        player_rows,
        failures,
        bad_teams,
        processed,
        successful_games,
    ) = _read_resume(args.resume_dir, season, common, source_gap_ids)

    prior_failures = len(failures)
    prior_bad_teams = sorted(bad_teams)
    resume_manifest = {
        "status": "CHECKPOINT_VALIDATED",
        "season": season,
        "resume_run_id": str(args.resume_run_id),
        "resume_dir": str(args.resume_dir),
        "required_full_core_keys": len(targets),
        "common_games": len(common),
        "processed_games_before_resume": processed,
        "successful_games_before_resume": successful_games,
        "failures_before_resume": prior_failures,
        "bad_teams_before_resume": prior_bad_teams,
        "remaining_common_games": len(common) - processed,
        "deterministic_prefix_validated": True,
        "source_gap_universe_validated": True,
    }
    (outdir / "resume_manifest.json").write_text(
        json.dumps(resume_manifest, indent=2) + "\n", encoding="utf-8"
    )

    core.write_progress(
        progress_path,
        {
            "phase": "RESUME_RECONSTRUCT_GAMES",
            "season": season,
            "required_keys": len(targets),
            "nba_games": len(nids),
            "v3_games": len(vids),
            "pbp_games": len(pids),
            "common_games": len(common),
            "source_gap_games": len(source_gap_ids),
            "processed_games": processed,
            "successful_games": successful_games,
            "failed_games": len(failures),
            "bad_teams": len(bad_teams),
            "team_fact_rows": len(team_rows),
            "player_fact_rows": len(player_rows),
            "remaining_common_games": len(common) - processed,
            "resume": True,
        },
    )

    audits: list[dict] = []
    for i, gid in enumerate(common[processed:], processed + 1):
        try:
            tr, pr, audit = base.build_game(gid, ng[gid], vg[gid], pg[gid])
            team_rows.extend(tr)
            player_rows.extend(pr)
            audits.append(audit)
            successful_games += 1
        except Exception as exc:
            teams = core.nba_game_teams(ng[gid])
            bad_teams.update(teams)
            failures.append(
                {
                    "game_id": gid,
                    "status": "RECONSTRUCTION_FAIL",
                    "teams": sorted(teams),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        if i % 25 == 0 or i == len(common):
            payload = {
                "phase": "RESUME_RECONSTRUCT_GAMES",
                "season": season,
                "required_keys": len(targets),
                "processed_games": i,
                "common_games": len(common),
                "successful_games": successful_games,
                "failed_games": len(failures),
                "bad_teams": len(bad_teams),
                "team_fact_rows": len(team_rows),
                "player_fact_rows": len(player_rows),
                "remaining_common_games": len(common) - i,
                "resume": True,
            }
            core.write_progress(progress_path, payload)
            core.checkpoint_facts(outdir, team_rows, player_rows, failures)
            print(json.dumps({"event": "RESUME_GAME_CHECKPOINT", **payload}), flush=True)

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    if team_df.empty or player_df.empty:
        raise RuntimeError(f"No reconstructed facts for {season}")

    successful_game_ids = set(
        pd.to_numeric(team_df["game_id"], errors="raise").astype("int64").tolist()
    )
    successful_games = len(successful_game_ids)
    expected_accounted = successful_game_ids | {
        int(r["game_id"])
        for r in failures
        if str(r.get("status")) == "RECONSTRUCTION_FAIL" and r.get("game_id") is not None
    }
    if expected_accounted != set(common):
        raise RuntimeError(
            f"Resume did not account for entire common-game universe: "
            f"accounted={len(expected_accounted)} common={len(common)}"
        )

    team_df.to_csv(outdir / "team_game_treb.csv.gz", index=False, compression="gzip")
    player_df.to_csv(outdir / "player_game_treb_on.csv.gz", index=False, compression="gzip")
    (outdir / "game_audit.resume_new_only.json").write_text(
        json.dumps(audits, indent=2) + "\n", encoding="utf-8"
    )
    (outdir / "game_failures.json").write_text(
        json.dumps(failures, indent=2) + "\n", encoding="utf-8"
    )

    core.write_progress(
        progress_path,
        {
            "phase": "AGGREGATE_FULLCORE",
            "season": season,
            "required_keys": len(targets),
            "successful_games": successful_games,
            "failed_games": len(failures),
            "bad_teams": len(bad_teams),
            "resume": True,
        },
    )

    tg = team_df.groupby("team_id", as_index=False).agg(
        game_seconds=("game_seconds", "sum"),
        team_oreb=("team_oreb", "sum"),
        team_dreb=("team_dreb", "sum"),
        opponent_oreb=("opponent_oreb", "sum"),
        opponent_dreb=("opponent_dreb", "sum"),
        reconstructed_team_games=("game_id", "nunique"),
    )
    pgagg = player_df.groupby(["team_id", "player_id"], as_index=False).agg(
        seconds_on=("seconds_on", "sum"),
        team_oreb_on=("team_oreb_on", "sum"),
        team_dreb_on=("team_dreb_on", "sum"),
        opponent_oreb_on=("opponent_oreb_on", "sum"),
        opponent_dreb_on=("opponent_dreb_on", "sum"),
        reconstructed_player_games=("game_id", "nunique"),
    )
    team_map = {int(r.team_id): r for r in tg.itertuples(index=False)}
    player_map = {
        (int(r.team_id), core.sid(r.player_id)): r for r in pgagg.itertuples(index=False)
    }

    out: list[dict] = []
    for t in targets:
        tid, pid = int(t["team_id"]), core.sid(t["player_id"])
        row = {
            "season": season,
            "team_id": tid,
            "player_id": pid,
            "player": t.get("player", ""),
            "target_minutes_on": float(t.get("seconds_on", 0.0)) / 60.0,
            "source": "fresh_exact_game_facts_shufinskiy_nba_v3_pbpstats_resumed",
            "rounded_percentage_backsolve_used": False,
            "opponent_rebound_inference_used": False,
            "whole_team_subtraction_across_partial_tenure_used": False,
        }
        if tid in bad_teams:
            row.update(
                status="TEAM_GAME_GAP",
                error="At least one team game had source/reconstruction failure",
            )
            out.append(row)
            continue
        team = team_map.get(tid)
        player = player_map.get((tid, pid))
        if team is None:
            row.update(status="MISSING_TEAM_TOTAL", error="No reconstructed team total")
            out.append(row)
            continue
        if player is None:
            row.update(
                status="MISSING_PLAYER_TOTAL",
                error="No reconstructed positive-second player row",
            )
            out.append(row)
            continue
        try:
            target_seconds = float(t.get("seconds_on", 0.0))
            actual_seconds = float(player.seconds_on)
            if abs(actual_seconds - target_seconds) > 60.0:
                raise ValueError(
                    f"minutes mismatch actual={actual_seconds/60:.6f} "
                    f"target={target_seconds/60:.6f}"
                )

            team_on = float(player.team_oreb_on + player.team_dreb_on)
            opp_on = float(player.opponent_oreb_on + player.opponent_dreb_on)
            team_total = float(team.team_oreb + team.team_dreb)
            opp_total = float(team.opponent_oreb + team.opponent_dreb)
            team_off = team_total - team_on
            opp_off = opp_total - opp_on
            if min(team_off, opp_off) < 0:
                raise ValueError(
                    f"negative off count team_off={team_off} opp_off={opp_off}"
                )
            treb_on = core.ratio(team_on, opp_on)
            treb_off = core.ratio(team_off, opp_off)
            if not (0.25 <= treb_on <= 0.75 and 0.25 <= treb_off <= 0.75):
                raise ValueError(f"implausible TREB on={treb_on} off={treb_off}")
            seconds_off = float(team.game_seconds) - actual_seconds
            if seconds_off < 0:
                raise ValueError(f"negative seconds_off={seconds_off}")

            row.update(
                {
                    "direct_treb_on": treb_on,
                    "direct_treb_off": treb_off,
                    "direct_minutes_on": actual_seconds / 60.0,
                    "direct_minutes_off": seconds_off / 60.0,
                    "team_oreb_on": int(player.team_oreb_on),
                    "team_dreb_on": int(player.team_dreb_on),
                    "opponent_oreb_on": int(player.opponent_oreb_on),
                    "opponent_dreb_on": int(player.opponent_dreb_on),
                    "team_oreb_off": int(team.team_oreb - player.team_oreb_on),
                    "team_dreb_off": int(team.team_dreb - player.team_dreb_on),
                    "opponent_oreb_off": int(
                        team.opponent_oreb - player.opponent_oreb_on
                    ),
                    "opponent_dreb_off": int(
                        team.opponent_dreb - player.opponent_dreb_on
                    ),
                    "reconstructed_team_games": int(team.reconstructed_team_games),
                    "reconstructed_player_games": int(
                        player.reconstructed_player_games
                    ),
                    "status": "PASS",
                    "error": "",
                }
            )
        except Exception as exc:
            row.update(
                status="INVALID_EXACT_ROW",
                error=f"{type(exc).__name__}: {exc}",
            )
        out.append(row)

    result = pd.DataFrame(out)
    result["player_id"] = result.player_id.astype(str)
    result.to_csv(outdir / f"fresh_fullcore_{args.year}.csv", index=False)

    pass_rows = int(result.status.astype(str).eq("PASS").sum())
    fail_rows = int(len(result) - pass_rows)
    qa = {
        "status": "PASS" if fail_rows == 0 else "INCOMPLETE",
        "season": season,
        "required_full_core_keys": len(targets),
        "output_rows": len(result),
        "pass_rows": pass_rows,
        "failure_rows": fail_rows,
        "nba_games": len(nids),
        "v3_games": len(vids),
        "pbp_games": len(pids),
        "common_games": len(common),
        "successful_games": successful_games,
        "source_or_reconstruction_failures": len(failures),
        "bad_teams": sorted(bad_teams),
        "resumed_from_processed_games": processed,
        "resumed_remaining_games": len(common) - processed,
        "resume_run_id": str(args.resume_run_id),
        "rounded_percentage_backsolve_used": False,
        "opponent_rebound_inference_used": False,
        "whole_team_subtraction_across_partial_tenure_used": False,
        "full_core_only": True,
    }
    (outdir / "season_gate.json").write_text(
        json.dumps(qa, indent=2) + "\n", encoding="utf-8"
    )
    core.write_progress(progress_path, {"phase": "COMPLETE", **qa})
    print(json.dumps(qa, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
