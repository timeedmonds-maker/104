#!/usr/bin/env python3
"""Checkpointed game-once reconstruction of V2 partial TREB tenure segments."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import numpy as np
import pandas as pd

from production_treb_engine import reconstruct_game_lineups, join_pbp_rebounds, classify_rebounds

BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
TARGETS_PATH = IMPACT / "roster_tenure_v2" / "wowy_partial_segments.jsonl.gz"
SCHEDULE_PATH = IMPACT / "roster_tenure" / "regular_season_games.jsonl.gz"
DEFAULT_RAW = IMPACT / "local_raw"
DEFAULT_OUT = IMPACT / "final_local_rebuild"

COUNT_KEYS = (
    "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on",
    "team_oreb_off", "team_dreb_off", "opponent_oreb_off", "opponent_dreb_off",
)


def json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def read_jsonl_gz(path: Path) -> list[dict]:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def atomic_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, sort_keys=True, indent=2, default=json_default) + "\n").encode()
    digest = hashlib.sha256(data).hexdigest()
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return digest


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE.parent, text=True).strip()
    except Exception:
        return "unknown"


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


def season_name(year: int) -> str:
    return f"{year}-{str((year + 1) % 100).zfill(2)}"


def game_duration_seconds(nba_game: pd.DataFrame) -> int:
    return 2880 + max(0, int(nba_game.PERIOD.max()) - 4) * 300


def new_accumulator(target: dict) -> dict:
    out = dict(target)
    out.update({"seconds_on": 0, "seconds_off": 0, "games_processed": 0})
    out.update({k: 0 for k in COUNT_KEYS})
    return out


def update_target(acc: dict, target: dict, joined: pd.DataFrame, lineups, game_seconds: int) -> None:
    pid = int(target["player_id"])
    team_abbr = str(target["team_abbr"])
    seconds_on = int(lineups.seconds.get(pid, 0))
    acc["seconds_on"] += seconds_on
    acc["seconds_off"] += max(0, int(game_seconds) - seconds_on)
    acc["games_processed"] += 1
    if joined.empty:
        return
    on = joined.LINEUP.map(lambda x: pid in x)
    off = ~on
    team_offense = ~joined.OPPONENT.astype(str).eq(team_abbr)
    team_defense = ~team_offense
    oreb = joined.IS_OREB.astype(bool)
    real = joined.IS_REAL_REBOUND.astype(bool)
    dreb = real & ~oreb
    acc["team_oreb_on"] += int((on & team_offense & oreb).sum())
    acc["team_dreb_on"] += int((on & team_defense & dreb).sum())
    acc["opponent_oreb_on"] += int((on & team_defense & oreb).sum())
    acc["opponent_dreb_on"] += int((on & team_offense & dreb).sum())
    acc["team_oreb_off"] += int((off & team_offense & oreb).sum())
    acc["team_dreb_off"] += int((off & team_defense & dreb).sum())
    acc["opponent_oreb_off"] += int((off & team_defense & oreb).sum())
    acc["opponent_dreb_off"] += int((off & team_offense & dreb).sum())


def finalize_acc(acc: dict) -> dict:
    out = dict(acc)
    out["team_rebounds_on"] = out["team_oreb_on"] + out["team_dreb_on"]
    out["opponent_rebounds_on"] = out["opponent_oreb_on"] + out["opponent_dreb_on"]
    out["team_rebounds_off"] = out["team_oreb_off"] + out["team_dreb_off"]
    out["opponent_rebounds_off"] = out["opponent_oreb_off"] + out["opponent_dreb_off"]
    on_den = out["team_rebounds_on"] + out["opponent_rebounds_on"]
    off_den = out["team_rebounds_off"] + out["opponent_rebounds_off"]
    out["treb_on"] = out["team_rebounds_on"] / on_den if on_den else None
    out["treb_off"] = out["team_rebounds_off"] / off_den if off_den else None
    out["treb_swing_pp"] = ((out["treb_on"] - out["treb_off"]) * 100.0
                            if out["treb_on"] is not None and out["treb_off"] is not None else None)
    return out


def load_schedule_for_season(season: str) -> list[dict]:
    return [r for r in read_jsonl_gz(SCHEDULE_PATH) if r["season"] == season]


def build_game_target_index(targets: list[dict], schedule: list[dict]) -> dict[int, list[int]]:
    index: dict[int, list[int]] = {}
    parsed = [(pd.Timestamp(t["query_start_date"]).date(), pd.Timestamp(t["query_end_date"]).date()) for t in targets]
    for game in schedule:
        gid = int(game["game_id"])
        date = pd.Timestamp(game["game_date"]).date()
        teams = {int(game["home_team_id"]), int(game["away_team_id"])}
        active = []
        for i, t in enumerate(targets):
            a, b = parsed[i]
            if int(t["team_id"]) in teams and a <= date <= b:
                active.append(i)
        if active:
            index[gid] = active
    return index


def source_sha(raw: Path, year: int) -> dict:
    out = {}
    for stem in (f"nbastats_{year}", f"pbpstats_{year}"):
        arc = raw / f"{stem}.tar.xz"
        if arc.exists():
            out[arc.name] = hashlib.sha256(arc.read_bytes()).hexdigest()
    return out


def process_season(year: int, args, manifest: dict, commit: str) -> None:
    season = season_name(year)
    lock_path = args.output / "locked_seasons" / f"{season}.json"
    if lock_path.exists() and not args.force:
        print(f"{season}: LOCKED; skip", flush=True)
        return
    season_targets = [t for t in read_jsonl_gz(TARGETS_PATH) if t["season"] == season]
    if not season_targets:
        raise RuntimeError(f"no V2 partial targets for {season}")
    schedule = load_schedule_for_season(season)
    game_targets = build_game_target_index(season_targets, schedule)
    game_ids = sorted(game_targets)
    nba_path = args.raw / f"nbastats_{year}.csv"
    pbp_path = args.raw / f"pbpstats_{year}.csv"
    if not nba_path.exists() or not pbp_path.exists():
        raise FileNotFoundError(f"missing raw CSV pair for {season}: {nba_path}, {pbp_path}")
    nba = normalize_nba(pd.read_csv(nba_path, low_memory=False))
    pbp = normalize_pbp(pd.read_csv(pbp_path, low_memory=False))
    nba_groups = {int(g): x for g, x in nba[nba.GAME_ID.isin(game_ids)].groupby("GAME_ID", sort=False)}
    pbp_groups = {int(g): x for g, x in pbp[pbp.GAMEID.isin(game_ids)].groupby("GAMEID", sort=False)}
    acc = [new_accumulator(t) for t in season_targets]
    season_repairs, season_exceptions, unmatched = [], [], []
    batch_records = []
    checkpoints_dir = args.output / "checkpoints" / season
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    for batch_no, start in enumerate(range(0, len(game_ids), args.batch_size), 1):
        batch = game_ids[start:start + args.batch_size]
        ok = 0
        batch_exceptions, batch_unmatched, batch_repairs = [], [], []
        for gid in batch:
            try:
                nba_game, pbp_game = nba_groups.get(gid), pbp_groups.get(gid)
                if nba_game is None or pbp_game is None:
                    raise ValueError(f"source game missing nba={nba_game is not None} pbp={pbp_game is not None}")
                lineups = reconstruct_game_lineups(nba_game)
                joined, audit = join_pbp_rebounds(lineups, pbp_game)
                if audit["unmatched_rebound_bearing_rows"]:
                    batch_unmatched.extend(audit["unmatched_rows"])
                    raise ValueError(f"unmatched rebound-bearing rows={audit['unmatched_rebound_bearing_rows']}")
                joined = classify_rebounds(joined)
                duration = game_duration_seconds(nba_game)
                for target_index in game_targets[gid]:
                    update_target(acc[target_index], season_targets[target_index], joined, lineups, duration)
                batch_repairs.extend(lineups.repairs)
                if audit["manual_join_repairs"]:
                    batch_repairs.append({"game_id": gid, "type": "manual_join_repairs", "count": audit["manual_join_repairs"]})
                ok += 1
            except Exception as exc:
                batch_exceptions.append({"game_id": gid, "error": str(exc)})
        season_repairs.extend(batch_repairs)
        season_exceptions.extend(batch_exceptions)
        unmatched.extend(batch_unmatched)
        checkpoint = {"season": season, "batch_index": batch_no, "code_commit": commit,
                      "game_ids": batch, "games_ok": ok, "games_attempted": len(batch),
                      "exceptions": batch_exceptions, "unmatched_rebound_rows": batch_unmatched,
                      "repairs": batch_repairs, "target_accumulators": [finalize_acc(x) for x in acc]}
        cp_path = checkpoints_dir / f"batch_{batch_no:03d}.json"
        sha = atomic_json(cp_path, checkpoint)
        batch_records.append({"batch_index": batch_no, "game_ids": batch, "games_ok": ok,
                              "exceptions": len(batch_exceptions), "unmatched_rebound_rows": len(batch_unmatched),
                              "path": str(cp_path.relative_to(args.output)), "sha256": sha})
        print(f"{season} batch {batch_no}: {ok}/{len(batch)}", flush=True)

    season_state = {"season": season,
                    "status": "REPAIR_REQUIRED" if season_exceptions or unmatched else "COMPLETE",
                    "code_commit": commit, "target_count": len(season_targets), "games_required": len(game_ids),
                    "batches": batch_records, "exceptions": season_exceptions,
                    "unmatched_rebound_rows": unmatched, "repairs": season_repairs,
                    "source_archives_sha256": source_sha(args.raw, year),
                    "targets": [finalize_acc(x) for x in acc]}
    manifest.setdefault("target_segments", 5199)
    manifest.setdefault("batch_size", args.batch_size)
    manifest.setdefault("seasons", {})[season] = {"status": season_state["status"], "targets": len(season_targets),
        "games_required": len(game_ids), "exceptions": len(season_exceptions),
        "unmatched_rebound_rows": len(unmatched), "repairs": len(season_repairs), "batches": batch_records}
    atomic_json(args.output / "master_manifest.json", manifest)
    if season_exceptions or unmatched:
        atomic_json(args.output / "repair_queue" / f"{season}.json", season_state)
        raise RuntimeError(f"{season} repair queue: {len(season_exceptions)} games, {len(unmatched)} rebound rows")
    digest = atomic_json(lock_path, season_state)
    manifest["seasons"][season]["lock_sha256"] = digest
    atomic_json(args.output / "master_manifest.json", manifest)
    print(f"COMPLETE {len(season_targets)} {len(season_repairs)} {digest}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2000, 2025)), help="season start years")
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "master_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"target_segments": 5199, "batch_size": args.batch_size, "seasons": {}}
    commit = git_commit()
    for year in args.years:
        process_season(year, args, manifest, commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
