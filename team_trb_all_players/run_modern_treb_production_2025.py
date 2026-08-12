#!/usr/bin/env python3
"""Final 2025-26 TREB production from the static NBA CDN play-by-play archive.

This is the modern companion to run_local_treb_production.py.  It writes the
same target accumulator schema and lock layout, but uses:
- explicit CDN substitution in/out rows for lineup reconstruction;
- the 2024 bridge-validated modern live/dead team-rebound rule;
- rebound-team versus immediately preceding missed-shot team for OREB/DREB.

No events are fabricated.  Any unresolved game is written to the repair queue
and prevents the season lock from being created.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

import modern_cdn_lineups as modern
import validate_modern_team_rebound_rule_2024 as rebound_bridge
import run_local_treb_production as base

YEAR = 2025
SEASON = "2025-26"
BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
DEFAULT_RAW = IMPACT / "local_raw"
DEFAULT_OUT = IMPACT / "final_local_rebuild"
PLAYER_MAX = modern.PLAYER_MAX

# Frozen, already-inspected 2024 bridge evidence.  This is audit metadata, not
# a gate: the project explicitly accepted this bounded modern approximation so
# final completion would not be held indefinitely on team/nonplayer semantics.
MODERN_BRIDGE_AUDIT = {
    "bridge_season": "2024-25",
    "team_or_nonplayer_rebound_rows": 20183,
    "candidate_rule_mismatches": 1047,
    "all_rebound_events_reference": 128700,
    "lineup_player_game_rows_within_2_01_seconds": 26234,
    "lineup_player_game_rows_over_2_01_seconds": 26,
    "policy": "accepted_bounded_modern_bridge_approximation",
}


def norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def normalize_cdn(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "orderNumber" not in out:
        out["orderNumber"] = out["actionNumber"]
    for col in ("gameId", "period", "actionNumber", "orderNumber", "personId", "teamId"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["gameId"] = out.gameId.astype("int64")
    out["period"] = out.period.astype("int64")
    out["actionNumber"] = out.actionNumber.astype("int64")
    out["orderNumber"] = out.orderNumber.fillna(out.actionNumber).astype("int64")
    return out


def is_missed_shot(row: pd.Series) -> bool:
    action = norm(row.get("actionType"))
    desc = norm(row.get("description"))
    result = norm(row.get("shotResult"))
    if result in {"miss", "missed"}:
        return True
    if "miss" not in desc:
        return False
    return any(token in action for token in ("2pt", "3pt", "shot", "free throw", "freethrow")) or "free throw" in desc


def rebound_team_id(row: pd.Series, teams: list[int]) -> int:
    team = int(row.teamId) if pd.notna(row.get("teamId")) else 0
    if team in teams:
        return team
    # Some team/nonplayer rows may expose a team-shaped personId.
    pid = int(row.personId) if pd.notna(row.get("personId")) else 0
    if pid in teams:
        return pid
    raise ValueError(
        f"cannot resolve modern rebound team game={int(row.gameId)} action={int(row.actionNumber)} "
        f"teamId={row.get('teamId')} personId={row.get('personId')} teams={teams}"
    )


def classify_game_rebounds(events: pd.DataFrame, teams: list[int]) -> pd.DataFrame:
    g = events.sort_values(["period", "orderNumber", "actionNumber"], kind="stable").copy()
    live = rebound_bridge.modern_candidate_rule(g)
    g["IS_REAL_REBOUND"] = live.reindex(g.index).fillna(True).astype(bool)
    action = g.actionType.astype("string").fillna("").str.lower()
    reb = g[action.eq("rebound")].copy()
    if reb.empty:
        return reb.assign(REBOUND_TEAM_ID=pd.Series(dtype="int64"), IS_OREB=pd.Series(dtype=bool))

    # Reconstruct the OREB/DREB label from team possession evidence.  The live
    # rebound's team equals the shooting team for an offensive rebound.
    ordered_indices = list(g.index)
    position = {idx: pos for pos, idx in enumerate(ordered_indices)}
    team_ids: list[int] = []
    orebs: list[bool] = []
    shot_events: list[int | None] = []

    for idx, row in reb.iterrows():
        rteam = rebound_team_id(row, teams)
        team_ids.append(rteam)
        if not bool(row.IS_REAL_REBOUND):
            orebs.append(False)
            shot_events.append(None)
            continue
        pos = position[idx]
        period = int(row.period)
        miss_team = 0
        miss_event: int | None = None
        for scan in range(pos - 1, -1, -1):
            cand = g.loc[ordered_indices[scan]]
            if int(cand.period) != period:
                break
            if not is_missed_shot(cand):
                continue
            cand_team = int(cand.teamId) if pd.notna(cand.get("teamId")) else 0
            if cand_team in teams:
                miss_team = cand_team
                miss_event = int(cand.actionNumber)
                break
        if not miss_team:
            raise ValueError(
                f"no prior missed-shot team for live modern rebound game={int(row.gameId)} "
                f"action={int(row.actionNumber)} period={period} clock={row.clock}"
            )
        orebs.append(rteam == miss_team)
        shot_events.append(miss_event)

    reb["REBOUND_TEAM_ID"] = team_ids
    reb["IS_OREB"] = orebs
    reb["PRIOR_MISS_ACTION"] = shot_events
    return reb


def game_duration_seconds(game: pd.DataFrame) -> int:
    return 2880 + max(0, int(game.period.max()) - 4) * 300


def update_target(acc: dict, target: dict, rebounds: pd.DataFrame, lineups: modern.ModernGameLineups, game_seconds: int) -> None:
    pid = int(target["player_id"])
    team_id = int(target["team_id"])
    seconds_on = int(round(float(lineups.seconds.get(pid, 0.0))))
    acc["seconds_on"] += seconds_on
    acc["seconds_off"] += max(0, int(game_seconds) - seconds_on)
    acc["games_processed"] += 1
    if rebounds.empty:
        return

    on = rebounds.LINEUP.map(lambda x: pid in x)
    off = ~on
    real = rebounds.IS_REAL_REBOUND.astype(bool)
    oreb = rebounds.IS_OREB.astype(bool)
    dreb = real & ~oreb
    own = rebounds.REBOUND_TEAM_ID.astype("int64").eq(team_id)
    opp = ~own

    acc["team_oreb_on"] += int((on & own & oreb).sum())
    acc["team_dreb_on"] += int((on & own & dreb).sum())
    acc["opponent_oreb_on"] += int((on & opp & oreb).sum())
    acc["opponent_dreb_on"] += int((on & opp & dreb).sum())
    acc["team_oreb_off"] += int((off & own & oreb).sum())
    acc["team_dreb_off"] += int((off & own & dreb).sum())
    acc["opponent_oreb_off"] += int((off & opp & oreb).sum())
    acc["opponent_dreb_off"] += int((off & opp & dreb).sum())


def source_sha(raw: Path) -> dict:
    out = {}
    for candidate in (raw / "cdnnba_2025.tar.xz", raw / "cdnnba_2025.csv"):
        if candidate.exists():
            out[candidate.name] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lock_path = args.output / "locked_seasons" / f"{SEASON}.json"
    if lock_path.exists() and not args.force:
        print(f"{SEASON}: LOCKED; skip", flush=True)
        return 0

    targets = [t for t in base.read_jsonl_gz(base.TARGETS_PATH) if t["season"] == SEASON]
    if not targets:
        raise RuntimeError(f"no V2 partial targets for {SEASON}")
    schedule = base.load_schedule_for_season(SEASON)
    if not schedule:
        raise RuntimeError(f"no regular-season schedule rows for {SEASON}")
    game_targets = base.build_game_target_index(targets, schedule)
    game_ids = sorted(game_targets)
    if not game_ids:
        raise RuntimeError(f"no target-bearing games for {SEASON}")

    cdn_path = args.raw / "cdnnba_2025.csv"
    if not cdn_path.exists():
        raise FileNotFoundError(cdn_path)
    cdn = normalize_cdn(pd.read_csv(cdn_path, low_memory=False))
    groups = {int(g): x for g, x in cdn[cdn.gameId.isin(game_ids)].groupby("gameId", sort=False)}

    acc = [base.new_accumulator(t) for t in targets]
    season_repairs: list[dict] = []
    season_exceptions: list[dict] = []
    batch_records: list[dict] = []
    checkpoints_dir = args.output / "checkpoints" / SEASON
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    commit = base.git_commit()

    for batch_no, start in enumerate(range(0, len(game_ids), args.batch_size), 1):
        batch = game_ids[start:start + args.batch_size]
        ok = 0
        batch_exceptions: list[dict] = []
        batch_repairs: list[dict] = []
        for gid in batch:
            try:
                game = groups.get(gid)
                if game is None:
                    raise ValueError("source game missing cdn=False")
                lineup = modern.reconstruct_game_lineups(game)
                rebounds = classify_game_rebounds(lineup.events, lineup.teams)
                duration = game_duration_seconds(game)
                for target_index in game_targets[gid]:
                    update_target(acc[target_index], targets[target_index], rebounds, lineup, duration)
                if lineup.repairs:
                    batch_repairs.append({
                        "game_id": int(gid),
                        "type": "modern_lineup_audit",
                        "entries": len(lineup.repairs),
                    })
                ok += 1
            except Exception as exc:
                batch_exceptions.append({"game_id": int(gid), "error": str(exc)})
        season_repairs.extend(batch_repairs)
        season_exceptions.extend(batch_exceptions)
        checkpoint = {
            "season": SEASON,
            "batch_index": batch_no,
            "code_commit": commit,
            "game_ids": batch,
            "games_ok": ok,
            "games_attempted": len(batch),
            "exceptions": batch_exceptions,
            "repairs": batch_repairs,
            "target_accumulators": [base.finalize_acc(x) for x in acc],
        }
        cp_path = checkpoints_dir / f"batch_{batch_no:03d}.json"
        sha = base.atomic_json(cp_path, checkpoint)
        batch_records.append({
            "batch_index": batch_no,
            "game_ids": batch,
            "games_ok": ok,
            "exceptions": len(batch_exceptions),
            "path": str(cp_path.relative_to(args.output)),
            "sha256": sha,
        })
        print(f"{SEASON} batch {batch_no}: {ok}/{len(batch)} exceptions={len(batch_exceptions)}", flush=True)

    state = {
        "season": SEASON,
        "status": "REPAIR_REQUIRED" if season_exceptions else "COMPLETE",
        "engine": "modern_cdn_explicit_substitutions",
        "code_commit": commit,
        "target_count": len(targets),
        "games_required": len(game_ids),
        "exceptions": season_exceptions,
        "accepted_excluded_games": [],
        "unmatched_rebound_rows": [],
        "repairs": season_repairs,
        "modern_bridge_audit": MODERN_BRIDGE_AUDIT,
        "source_archives_sha256": source_sha(args.raw),
        "batches": batch_records,
        "targets": [base.finalize_acc(x) for x in acc],
    }

    manifest_path = args.output / "master_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"target_segments": 5199, "seasons": {}}
    manifest.setdefault("seasons", {})[SEASON] = {
        "status": state["status"],
        "targets": len(targets),
        "games_required": len(game_ids),
        "exceptions": len(season_exceptions),
        "repairs": len(season_repairs),
        "modern_bridge_policy": MODERN_BRIDGE_AUDIT["policy"],
        "batches": batch_records,
    }
    base.atomic_json(manifest_path, manifest)

    if season_exceptions:
        base.atomic_json(args.output / "repair_queue" / f"{SEASON}.json", state)
        raise RuntimeError(f"{SEASON} repair queue: {len(season_exceptions)} games")

    digest = base.atomic_json(lock_path, state)
    manifest["seasons"][SEASON]["lock_sha256"] = digest
    base.atomic_json(manifest_path, manifest)
    print(f"COMPLETE {len(targets)} {len(season_repairs)} {digest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
