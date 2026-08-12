#!/usr/bin/env python3
"""Reconcile exact per-game TREB facts to the hard 14,524 PTS universe.

This script is intentionally fail-closed. It will not publish final PTS rows if:
- a required season fact layer is incomplete;
- a target PTS has no audited tenure window;
- any positive player-game falls outside the audited tenure game universe;
- exact reconstructed ON seconds disagree with the locked core; or
- the hard 14,524 target identity changes.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

ORPHAN_KEYS = {
    ("2000-01", 1610612747, 145),
    ("2003-04", 1610612746, 1917),
}


def _read_json_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _load_targets(core_root: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(core_root.glob("*/*.json.gz")):
        payload = _read_json_gz(path)
        if payload.get("absent_team_season"):
            continue
        for row in payload.get("rebound_derived", []):
            sec = int(round(float(row.get("seconds") or 0)))
            if sec <= 0:
                continue
            season = str(row.get("season") or payload.get("season"))
            team_id = int(row.get("team_id") or payload.get("team_id"))
            player_id = int(row["player_id"])
            key = (season, team_id, player_id)
            if key in ORPHAN_KEYS:
                continue
            rows.append({
                "season": season,
                "team_id": team_id,
                "player_id": player_id,
                "player": str(row.get("player") or ""),
                "locked_seconds_on": sec,
            })
    out = pd.DataFrame(rows).drop_duplicates(["season", "team_id", "player_id"])
    if len(out) != 14524:
        raise SystemExit(f"HARD_GATE target universe changed: {len(out)} != 14524")
    return out.sort_values(["season", "team_id", "player_id"]).reset_index(drop=True)


def _load_windows(path: Path) -> pd.DataFrame:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    for c in ("player_id", "team_id"):
        df[c] = pd.to_numeric(df[c], errors="raise").astype(int)
    for c in ("query_start_date", "query_end_date", "tenure_start", "tenure_end"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.normalize()
    return df


def _load_facts(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    team_frames = []
    player_frames = []
    qas = []
    for season_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        qa_path = season_dir / "qa.json"
        team_path = season_dir / "team_game_treb.csv.gz"
        player_path = season_dir / "player_game_treb_on.csv.gz"
        if not qa_path.exists():
            continue
        qa = json.loads(qa_path.read_text())
        qas.append(qa)
        if team_path.exists():
            frame = pd.read_csv(team_path)
            frame["season"] = season_dir.name
            team_frames.append(frame)
        if player_path.exists():
            frame = pd.read_csv(player_path)
            frame["season"] = season_dir.name
            player_frames.append(frame)
    team = pd.concat(team_frames, ignore_index=True) if team_frames else pd.DataFrame()
    player = pd.concat(player_frames, ignore_index=True) if player_frames else pd.DataFrame()
    for frame in (team, player):
        if not frame.empty:
            frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce").dt.normalize()
            frame["game_id"] = pd.to_numeric(frame["game_id"], errors="raise").astype(int)
            frame["team_id"] = pd.to_numeric(frame["team_id"], errors="raise").astype(int)
    if not player.empty:
        player["player_id"] = pd.to_numeric(player["player_id"], errors="raise").astype(int)
    return team, player, qas


def _ratio(num: int, den: int):
    return None if den <= 0 else num / den


def _career(pts: pd.DataFrame) -> pd.DataFrame:
    count_cols = [
        "seconds_on", "seconds_off",
        "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on",
        "team_oreb_off", "team_dreb_off", "opponent_oreb_off", "opponent_dreb_off",
    ]
    names = pts.sort_values(["player_id", "season"]).groupby("player_id", as_index=False).agg(player=("player", "last"))
    agg = pts.groupby("player_id", as_index=False)[count_cols].sum().merge(names, on="player_id", how="left")
    agg["minutes_on"] = agg.seconds_on / 60.0
    agg["minutes_off"] = agg.seconds_off / 60.0
    on_team = agg.team_oreb_on + agg.team_dreb_on
    on_opp = agg.opponent_oreb_on + agg.opponent_dreb_on
    off_team = agg.team_oreb_off + agg.team_dreb_off
    off_opp = agg.opponent_oreb_off + agg.opponent_dreb_off
    agg["treb_on"] = on_team / (on_team + on_opp)
    agg["treb_off"] = off_team / (off_team + off_opp)
    agg["treb_swing_pp"] = 100.0 * (agg.treb_on - agg.treb_off)
    return agg.sort_values(["minutes_on", "player_id"], ascending=[False, True]).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-root", type=Path, required=True)
    ap.add_argument("--tenure-windows", type=Path, required=True)
    ap.add_argument("--facts-root", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--expected-seasons", type=int, default=26)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    targets = _load_targets(args.core_root)
    windows = _load_windows(args.tenure_windows)
    team_games, player_games, qas = _load_facts(args.facts_root)

    qa_failures = [q for q in qas if q.get("status") != "PASS"]
    season_names = sorted(set(q.get("season") for q in qas if q.get("season")))
    blockers = []
    if len(season_names) != args.expected_seasons:
        blockers.append({"kind": "season_count", "observed": len(season_names), "expected": args.expected_seasons})
    for q in qa_failures:
        blockers.append({"kind": "game_fact_season_incomplete", "season": q.get("season"), "qa": q})

    target_keys = set(map(tuple, targets[["season", "team_id", "player_id"]].itertuples(index=False, name=None)))
    windows = windows[windows[["season", "team_id", "player_id"]].apply(tuple, axis=1).isin(target_keys)].copy()

    pts_rows = []
    coverage_rows = []
    for idx, target in targets.iterrows():
        season = target.season
        team_id = int(target.team_id)
        player_id = int(target.player_id)
        w = windows[(windows.season.eq(season)) & (windows.team_id.eq(team_id)) & (windows.player_id.eq(player_id))].copy()
        if w.empty:
            coverage_rows.append({"season": season, "team_id": team_id, "player_id": player_id, "kind": "no_tenure_window"})
            continue

        tg = team_games[(team_games.season.eq(season)) & (team_games.team_id.eq(team_id))].copy()
        pg = player_games[(player_games.season.eq(season)) & (player_games.team_id.eq(team_id)) & (player_games.player_id.eq(player_id))].copy()
        if tg.empty:
            coverage_rows.append({"season": season, "team_id": team_id, "player_id": player_id, "kind": "no_team_game_facts"})
            continue

        covered_ids: set[int] = set()
        for _, win in w.iterrows():
            start = win.get("query_start_date")
            end = win.get("query_end_date")
            if pd.isna(start) or pd.isna(end) or start > end:
                coverage_rows.append({
                    "season": season, "team_id": team_id, "player_id": player_id,
                    "kind": "invalid_query_window", "start": None if pd.isna(start) else str(start.date()),
                    "end": None if pd.isna(end) else str(end.date()),
                })
                continue
            selected = tg[tg.game_date.between(start, end, inclusive="both")]
            covered_ids.update(int(x) for x in selected.game_id)

        played_ids = set(int(x) for x in pg.loc[pg.seconds_on.gt(0), "game_id"])
        uncovered = sorted(played_ids - covered_ids)
        if uncovered:
            coverage_rows.append({
                "season": season, "team_id": team_id, "player_id": player_id,
                "kind": "positive_on_game_outside_tenure", "game_ids": ";".join(map(str, uncovered)),
            })
            continue
        if not covered_ids:
            coverage_rows.append({"season": season, "team_id": team_id, "player_id": player_id, "kind": "zero_covered_team_games"})
            continue

        tenure_games = tg[tg.game_id.isin(covered_ids)].copy()
        on_games = pg[pg.game_id.isin(covered_ids)].copy()
        seconds_on = int(on_games.seconds_on.sum())
        locked_seconds = int(target.locked_seconds_on)
        if seconds_on != locked_seconds:
            coverage_rows.append({
                "season": season, "team_id": team_id, "player_id": player_id,
                "kind": "locked_seconds_mismatch", "reconstructed_seconds": seconds_on,
                "locked_seconds": locked_seconds, "difference": seconds_on - locked_seconds,
            })
            continue

        totals = {c: int(tenure_games[c].sum()) for c in ("game_seconds", "team_oreb", "team_dreb", "opponent_oreb", "opponent_dreb")}
        on = {c: int(on_games[c].sum()) for c in ("team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on")}
        off = {
            "team_oreb_off": totals["team_oreb"] - on["team_oreb_on"],
            "team_dreb_off": totals["team_dreb"] - on["team_dreb_on"],
            "opponent_oreb_off": totals["opponent_oreb"] - on["opponent_oreb_on"],
            "opponent_dreb_off": totals["opponent_dreb"] - on["opponent_dreb_on"],
        }
        if min(off.values()) < 0:
            coverage_rows.append({"season": season, "team_id": team_id, "player_id": player_id, "kind": "negative_off_rebound_count"})
            continue
        seconds_off = int(totals["game_seconds"] - seconds_on)
        if seconds_off < 0:
            coverage_rows.append({"season": season, "team_id": team_id, "player_id": player_id, "kind": "negative_off_seconds"})
            continue

        team_on = on["team_oreb_on"] + on["team_dreb_on"]
        opp_on = on["opponent_oreb_on"] + on["opponent_dreb_on"]
        team_off = off["team_oreb_off"] + off["team_dreb_off"]
        opp_off = off["opponent_oreb_off"] + off["opponent_dreb_off"]
        treb_on = _ratio(team_on, team_on + opp_on)
        treb_off = _ratio(team_off, team_off + opp_off)
        pts_rows.append({
            "season": season,
            "team_id": team_id,
            "player_id": player_id,
            "player": target.player,
            "tenure_window_count": int(len(w)),
            "tenure_team_games": int(len(covered_ids)),
            "seconds_on": seconds_on,
            "minutes_on": seconds_on / 60.0,
            "seconds_off": seconds_off,
            "minutes_off": seconds_off / 60.0,
            **on, **off,
            "treb_on": treb_on,
            "treb_off": treb_off,
            "treb_swing_pp": None if treb_on is None or treb_off is None else 100.0 * (treb_on - treb_off),
        })
        if (idx + 1) % 1000 == 0:
            print(f"RECONCILE_PROGRESS {idx+1}/14524 pass={len(pts_rows)} blockers={len(coverage_rows)}", flush=True)

    coverage = pd.DataFrame(coverage_rows)
    coverage.to_csv(args.output_dir / "tenure_coverage_blockers.csv", index=False)
    if coverage_rows:
        blockers.append({"kind": "pts_coverage_blockers", "count": len(coverage_rows)})

    gate = {
        "hard_target_pts": 14524,
        "target_rows": int(len(targets)),
        "tenure_windows_for_target_keys": int(len(windows)),
        "fact_seasons": season_names,
        "fact_season_count": len(season_names),
        "provisional_pts_rows": len(pts_rows),
        "coverage_blocker_rows": len(coverage_rows),
        "blockers": blockers,
        "status": "PASS" if not blockers and len(pts_rows) == 14524 else "BLOCKED",
    }
    (args.output_dir / "reconciliation_qa.json").write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(gate, indent=2), flush=True)

    if gate["status"] != "PASS":
        return 2

    pts = pd.DataFrame(pts_rows).sort_values(["season", "team_id", "player_id"]).reset_index(drop=True)
    pts.to_csv(args.output_dir / "treb_player_team_season.csv", index=False)
    career = _career(pts)
    career.to_csv(args.output_dir / "career_treb_summary.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
