#!/usr/bin/env python3
"""Expedited, auditable final historical TREB production runner.

This runner keeps the recovered production engine intact and makes only two
explicit completion concessions:
  1. exactly 39 previously diagnosed games are excluded from accumulation;
  2. the bounded completion rebound join policy may omit up to 250 unmatched
     rebound-bearing rows per season, with every omitted row logged.

No unknown game failure is accepted.  Any failure outside the exact allowlist,
any rebound mismatch above the bounded season limit, or any other production
exception still leaves the season REPAIR_REQUIRED.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import run_local_treb_production as base
import production_treb_engine_completion as engine


# Exact finite exception set from the completed 22-year targeted diagnostic
# aggregation (run 31547002431).  These are excluded proactively so no partial
# accumulator mutation can occur if a known bad lineup fails mid-game.
ACCEPTED_COMPLETION_EXCEPTIONS = {
    20201160: "starter_unresolved",
    20400335: "starter_unresolved",
    20400736: "starter_unresolved",
    20500090: "starter_unresolved",
    20500102: "starter_unresolved",
    20600887: "starter_unresolved",
    20700319: "starter_unresolved",
    20800032: "starter_unresolved",
    20800142: "starter_unresolved",
    21000431: "sub_out_absent",
    21000997: "sub_out_absent",
    21100842: "starter_unresolved",
    21200919: "sub_out_absent",
    21201167: "sub_out_absent",
    21301048: "starter_unresolved",
    21400968: "sub_out_absent",
    21500711: "sub_out_absent",
    21500903: "sub_out_absent",
    21500916: "lineup_size",
    21600358: "sub_out_absent",
    21600655: "starter_unresolved",
    21600668: "starter_unresolved",
    21701085: "sub_out_absent",
    21800143: "starter_unresolved",
    22000485: "starter_unresolved",
    22000853: "sub_out_absent",
    22100688: "starter_unresolved",
    22200140: "starter_unresolved",
    22200182: "lineup_size",
    22200207: "sub_out_absent",
    22200234: "starter_unresolved",
    22200778: "sub_out_absent",
    22201040: "starter_unresolved",
    22300452: "sub_out_absent",
    22300599: "sub_out_absent",
    22400433: "starter_unresolved",
    21901316: "legacy_source_missing_v3_present",
    21901317: "legacy_source_missing_v3_present",
    21901318: "legacy_source_missing_v3_present",
}
assert len(ACCEPTED_COMPLETION_EXCEPTIONS) == 39


def accepted_record(gid: int) -> dict:
    return {
        "game_id": int(gid),
        "type": "accepted_completion_game_exclusion",
        "diagnostic_signature": ACCEPTED_COMPLETION_EXCEPTIONS[int(gid)],
        "source": "targeted_exception_aggregate_run_31547002431",
        "resolution": "excluded_from_player_on_off_accumulation",
        "scope": "exact_game_allowlist_only",
    }


def process_season(year: int, args, manifest: dict, commit: str) -> None:
    season = base.season_name(year)
    lock_path = args.output / "locked_seasons" / f"{season}.json"
    repair_path = args.output / "repair_queue" / f"{season}.json"
    if lock_path.exists() and not args.force:
        print(f"{season}: LOCKED; skip", flush=True)
        return
    if args.force and repair_path.exists():
        repair_path.unlink()

    season_targets = [t for t in base.read_jsonl_gz(base.TARGETS_PATH) if t["season"] == season]
    if not season_targets:
        raise RuntimeError(f"no V2 partial targets for {season}")
    schedule = base.load_schedule_for_season(season)
    game_targets = base.build_game_target_index(season_targets, schedule)
    game_ids = sorted(game_targets)
    nba_path = args.raw / f"nbastats_{year}.csv"
    pbp_path = args.raw / f"pbpstats_{year}.csv"
    if not nba_path.exists() or not pbp_path.exists():
        raise FileNotFoundError(f"missing raw CSV pair for {season}: {nba_path}, {pbp_path}")

    nba = base.normalize_nba(pd.read_csv(nba_path, low_memory=False))
    pbp = base.normalize_pbp(pd.read_csv(pbp_path, low_memory=False))
    nba_groups = {int(g): x for g, x in nba[nba.GAME_ID.isin(game_ids)].groupby("GAME_ID", sort=False)}
    pbp_groups = {int(g): x for g, x in pbp[pbp.GAMEID.isin(game_ids)].groupby("GAMEID", sort=False)}
    acc = [base.new_accumulator(t) for t in season_targets]
    season_repairs, season_exceptions, unmatched = [], [], []
    accepted_excluded = []
    batch_records = []
    checkpoints_dir = args.output / "checkpoints" / season
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    for batch_no, start in enumerate(range(0, len(game_ids), args.batch_size), 1):
        batch = game_ids[start:start + args.batch_size]
        ok = 0
        batch_exceptions, batch_unmatched, batch_repairs, batch_excluded = [], [], [], []
        for gid in batch:
            if gid in ACCEPTED_COMPLETION_EXCEPTIONS:
                rec = accepted_record(gid)
                batch_repairs.append(rec)
                batch_excluded.append(rec)
                accepted_excluded.append(rec)
                continue
            try:
                nba_game, pbp_game = nba_groups.get(gid), pbp_groups.get(gid)
                if nba_game is None or pbp_game is None:
                    raise ValueError(f"source game missing nba={nba_game is not None} pbp={pbp_game is not None}")
                lineups = engine.reconstruct_game_lineups(nba_game)
                joined, audit = engine.join_pbp_rebounds(lineups, pbp_game)
                if audit["unmatched_rebound_bearing_rows"]:
                    batch_unmatched.extend(audit["unmatched_rows"])
                    raise ValueError(f"unmatched rebound-bearing rows={audit['unmatched_rebound_bearing_rows']}")
                joined = engine.classify_rebounds(joined)
                duration = base.game_duration_seconds(nba_game)
                for target_index in game_targets[gid]:
                    base.update_target(acc[target_index], season_targets[target_index], joined, lineups, duration)
                batch_repairs.extend(lineups.repairs)
                if audit.get("manual_join_repairs"):
                    batch_repairs.append({"game_id": gid, "type": "manual_join_repairs", "count": audit["manual_join_repairs"]})
                ok += 1
            except Exception as exc:
                # There is intentionally no generic tolerance here.  The 39
                # accepted games were removed above; every other failure is new.
                batch_exceptions.append({"game_id": gid, "error": str(exc)})

        season_repairs.extend(batch_repairs)
        season_exceptions.extend(batch_exceptions)
        unmatched.extend(batch_unmatched)
        checkpoint = {
            "season": season,
            "batch_index": batch_no,
            "code_commit": commit,
            "completion_mode": True,
            "game_ids": batch,
            "games_ok": ok,
            "games_attempted": len(batch),
            "accepted_excluded_games": batch_excluded,
            "exceptions": batch_exceptions,
            "unmatched_rebound_rows": batch_unmatched,
            "repairs": batch_repairs,
            "target_accumulators": [base.finalize_acc(x) for x in acc],
        }
        cp_path = checkpoints_dir / f"batch_{batch_no:03d}.json"
        sha = base.atomic_json(cp_path, checkpoint)
        batch_records.append({
            "batch_index": batch_no,
            "game_ids": batch,
            "games_ok": ok,
            "accepted_excluded_games": len(batch_excluded),
            "exceptions": len(batch_exceptions),
            "unmatched_rebound_rows": len(batch_unmatched),
            "path": str(cp_path.relative_to(args.output)),
            "sha256": sha,
        })
        print(f"{season} batch {batch_no}: {ok}/{len(batch)} excluded={len(batch_excluded)}", flush=True)

    season_state = {
        "season": season,
        "status": "REPAIR_REQUIRED" if season_exceptions or unmatched else "COMPLETE",
        "completion_mode": True,
        "completion_policy": {
            "explicit_game_allowlist_size": 39,
            "season_accepted_excluded_games": len(accepted_excluded),
            "max_unmatched_rebound_rows_per_season": engine.MAX_ACCEPTED_UNMATCHED_REBOUND_ROWS_PER_SEASON,
            "unknown_game_failures_permitted": 0,
        },
        "code_commit": commit,
        "target_count": len(season_targets),
        "games_required": len(game_ids),
        "games_processed": len(game_ids) - len(accepted_excluded) - len(season_exceptions),
        "accepted_excluded_games": accepted_excluded,
        "batches": batch_records,
        "exceptions": season_exceptions,
        "unmatched_rebound_rows": unmatched,
        "repairs": season_repairs,
        "source_archives_sha256": base.source_sha(args.raw, year),
        "targets": [base.finalize_acc(x) for x in acc],
    }

    manifest.setdefault("target_segments", 5199)
    manifest.setdefault("batch_size", args.batch_size)
    manifest.setdefault("seasons", {})[season] = {
        "status": season_state["status"],
        "completion_mode": True,
        "targets": len(season_targets),
        "games_required": len(game_ids),
        "games_processed": season_state["games_processed"],
        "accepted_excluded_games": len(accepted_excluded),
        "exceptions": len(season_exceptions),
        "unmatched_rebound_rows": len(unmatched),
        "repairs": len(season_repairs),
        "batches": batch_records,
    }
    base.atomic_json(args.output / "master_manifest.json", manifest)

    if season_exceptions or unmatched:
        base.atomic_json(repair_path, season_state)
        raise RuntimeError(f"{season} repair queue: {len(season_exceptions)} new games, {len(unmatched)} rebound rows")

    digest = base.atomic_json(lock_path, season_state)
    manifest["seasons"][season]["lock_sha256"] = digest
    base.atomic_json(args.output / "master_manifest.json", manifest)
    print(
        f"COMPLETE targets={len(season_targets)} excluded_games={len(accepted_excluded)} "
        f"repairs={len(season_repairs)} sha256={digest}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2000, 2025)), help="season start years")
    parser.add_argument("--raw", type=Path, default=base.DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=base.DEFAULT_OUT)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "master_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "target_segments": 5199,
        "batch_size": args.batch_size,
        "seasons": {},
    }
    commit = base.git_commit()
    for year in args.years:
        process_season(year, args, manifest, commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
