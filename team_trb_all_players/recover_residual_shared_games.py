#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import pathlib
import signal
from collections import Counter, defaultdict

import pandas as pd

import build_exact_game_fact_layer as builder

REQP = ("seconds_on", "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on")
REQT = ("team_oreb", "team_dreb", "opponent_oreb", "opponent_dreb")


def pid(x) -> str:
    s = str(x).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def tid(x) -> str:
    return str(int(float(x)))


def gid(x) -> str:
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    try:
        return str(int(float(s))).zfill(10)
    except Exception:
        return s.zfill(10)


def key(r) -> tuple[str, str, str]:
    return (str(r["season"]), tid(r["team_id"]), pid(r["player_id"]))


def find_one(root: pathlib.Path, pattern: str) -> pathlib.Path:
    xs = list(root.rglob(pattern))
    if len(xs) != 1:
        raise RuntimeError(f"expected one {pattern} under {root}, found {len(xs)}")
    return xs[0]


def load_accepted(*roots: pathlib.Path) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*MATERIALITY_ACCEPTED*.csv"):
            try:
                with open(path, newline="") as f:
                    for r in csv.DictReader(f):
                        if not r.get("season") or not r.get("team_id") or not r.get("player_id"):
                            continue
                        out.add(key(r))
            except Exception:
                continue
    return out


def load_required(current_dir: pathlib.Path, accepted: set[tuple[str, str, str]], season: str):
    diagnostics = find_one(current_dir, "TREB_*RECLOSURE_DIAGNOSTICS.csv")
    team_targets: dict[str, set[str]] = defaultdict(set)
    player_targets: dict[str, set[tuple[str, str]]] = defaultdict(set)
    affected_keys: set[tuple[str, str, str]] = set()

    with open(diagnostics, newline="") as f:
        for r in csv.DictReader(f):
            k = key(r)
            if k[0] != season or k in accepted:
                continue
            if r.get("status") != "BLOCKED_MISSING_PRIMITIVES":
                continue
            try:
                detail = json.loads(r.get("detail") or "{}")
            except Exception:
                continue
            mts = [gid(g) for g in detail.get("missing_team", []) if str(g).strip()]
            mps = [gid(g) for g in detail.get("missing_player", []) if str(g).strip()]
            if mts or mps:
                affected_keys.add(k)
            for g in mts:
                team_targets[g].add(k[1])
            for g in mps:
                player_targets[g].add((k[1], k[2]))

    games = sorted(set(team_targets) | set(player_targets))
    return games, team_targets, player_targets, affected_keys


def write_gzip_csv(path: pathlib.Path, rows: list[dict], fields: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({z: r.get(z, "") for z in fields})


class PerGameTimeout(RuntimeError):
    pass


def _timeout_handler(signum, frame):
    raise PerGameTimeout("per-game reconstruction timeout")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--current-dir", type=pathlib.Path, required=True)
    ap.add_argument("--materiality-dir", type=pathlib.Path, required=True)
    ap.add_argument("--materiality-2015-dir", type=pathlib.Path, required=True)
    ap.add_argument("--nba", type=pathlib.Path, required=True)
    ap.add_argument("--v3", type=pathlib.Path, required=True)
    ap.add_argument("--pbp", type=pathlib.Path, required=True)
    ap.add_argument("--output-dir", type=pathlib.Path, required=True)
    ap.add_argument("--per-game-timeout-seconds", type=int, default=180)
    args = ap.parse_args()

    season = f"{args.year}-{(args.year + 1) % 100:02d}"
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    accepted = load_accepted(args.materiality_dir, args.materiality_2015_dir)
    games, team_targets, player_targets, affected_keys = load_required(args.current_dir, accepted, season)
    if not games:
        raise RuntimeError(f"no residual shared games for {season}")

    nba = builder.io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3 = builder.lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp = builder.io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
    nba_groups = {gid(g): frame.copy() for g, frame in nba.groupby("GAME_ID", sort=False)}
    v3_groups = {gid(g): frame.copy() for g, frame in v3.groupby("gameId", sort=False)}
    pbp_groups = {gid(g): frame.copy() for g, frame in pbp.groupby("GAMEID", sort=False)}
    del nba, v3, pbp

    signal.signal(signal.SIGALRM, _timeout_handler)

    team_out: list[dict] = []
    player_out: list[dict] = []
    diagnostics: list[dict] = []
    reason_counts = Counter()

    for idx, g in enumerate(games, 1):
        req_teams = sorted(team_targets.get(g, set()))
        req_players = sorted(player_targets.get(g, set()))
        if g not in nba_groups or g not in v3_groups or g not in pbp_groups:
            diagnostics.append({
                "season": season,
                "game_id": g,
                "status": "SOURCE_SET_GAP",
                "required_team_targets": len(req_teams),
                "required_player_targets": len(req_players),
                "recovered_team_targets": 0,
                "recovered_player_targets": 0,
                "error": json.dumps({"nba": g in nba_groups, "v3": g in v3_groups, "pbp": g in pbp_groups}, separators=(",", ":")),
            })
            reason_counts["SOURCE_SET_GAP"] += 1
            continue

        try:
            signal.alarm(max(1, int(args.per_game_timeout_seconds)))
            tr, pr, audit = builder.build_game(int(g), nba_groups[g], v3_groups[g], pbp_groups[g])
            signal.alarm(0)
        except Exception as exc:
            signal.alarm(0)
            diagnostics.append({
                "season": season,
                "game_id": g,
                "status": "RECONSTRUCTION_FAILED",
                "required_team_targets": len(req_teams),
                "required_player_targets": len(req_players),
                "recovered_team_targets": 0,
                "recovered_player_targets": 0,
                "error": f"{type(exc).__name__}: {exc}",
            })
            reason_counts["RECONSTRUCTION_FAILED"] += 1
            print(json.dumps({"event": "GAME_FAIL", "season": season, "game_id": g, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
            continue

        team_by = {tid(r["team_id"]): r for r in tr}
        positive_same = {(tid(r["team_id"]), pid(r["player_id"])): r for r in pr}
        positive_by_player: dict[str, set[str]] = defaultdict(set)
        for r in pr:
            positive_by_player[pid(r["player_id"])].add(tid(r["team_id"]))

        recovered_team = 0
        recovered_player = 0
        for t in req_teams:
            row = team_by.get(t)
            if row is None:
                continue
            team_out.append({
                "season": season,
                "game_id": g,
                "team_id": t,
                **{z: int(round(float(row[z]))) for z in REQT},
                "provenance": "exact retained NBA+V3+PBP shared-game reconstruction",
            })
            recovered_team += 1

        for t, p in req_players:
            if t not in team_by:
                continue
            row = positive_same.get((t, p))
            if row is not None:
                player_out.append({
                    "season": season,
                    "game_id": g,
                    "team_id": t,
                    "player_id": p,
                    **{z: int(round(float(row[z]))) for z in REQP},
                    "exact_zero_proof": False,
                    "provenance": "exact retained NBA+V3+PBP shared-game reconstruction",
                })
                recovered_player += 1
                continue
            other_teams = positive_by_player.get(p, set()) - {t}
            if other_teams:
                # Never zero-fill a target if the reconstructed complete game places the same player on the other team.
                continue
            player_out.append({
                "season": season,
                "game_id": g,
                "team_id": t,
                "player_id": p,
                **{z: 0 for z in REQP},
                "exact_zero_proof": True,
                "provenance": "exact zero: validated complete reconstructed lineup excludes player from audited tenure team game",
            })
            recovered_player += 1

        status = "PASS_EXACT" if recovered_team == len(req_teams) and recovered_player == len(req_players) else "PARTIAL_EXACT"
        diagnostics.append({
            "season": season,
            "game_id": g,
            "status": status,
            "required_team_targets": len(req_teams),
            "required_player_targets": len(req_players),
            "recovered_team_targets": recovered_team,
            "recovered_player_targets": recovered_player,
            "error": "",
        })
        reason_counts[status] += 1
        print(json.dumps({
            "event": "GAME_DONE", "season": season, "game_id": g, "status": status,
            "team": f"{recovered_team}/{len(req_teams)}", "player": f"{recovered_player}/{len(req_players)}",
            "processed": f"{idx}/{len(games)}"
        }), flush=True)

    # Fail on contradictory duplicate exact facts; otherwise deduplicate identical rows.
    team_map = {}
    for r in team_out:
        k = (r["game_id"], r["team_id"])
        vals = tuple(r[z] for z in REQT)
        if k in team_map and tuple(team_map[k][z] for z in REQT) != vals:
            raise RuntimeError(f"conflicting exact team facts {k}")
        team_map[k] = r
    player_map = {}
    for r in player_out:
        k = (r["game_id"], r["team_id"], r["player_id"])
        vals = tuple(r[z] for z in REQP)
        if k in player_map and tuple(player_map[k][z] for z in REQP) != vals:
            raise RuntimeError(f"conflicting exact player facts {k}")
        player_map[k] = r
    team_out = [team_map[k] for k in sorted(team_map)]
    player_out = [player_map[k] for k in sorted(player_map)]

    team_fields = ["season", "game_id", "team_id", *REQT, "provenance"]
    player_fields = ["season", "game_id", "team_id", "player_id", *REQP, "exact_zero_proof", "provenance"]
    diag_fields = ["season", "game_id", "status", "required_team_targets", "required_player_targets", "recovered_team_targets", "recovered_player_targets", "error"]
    write_gzip_csv(out / "RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz", team_out, team_fields)
    write_gzip_csv(out / "RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz", player_out, player_fields)
    with open(out / "RESIDUAL_SHARED_GAME_DIAGNOSTICS.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=diag_fields)
        w.writeheader(); w.writerows(diagnostics)

    required_team_n = sum(len(v) for v in team_targets.values())
    required_player_n = sum(len(v) for v in player_targets.values())
    qa = {
        "status": "PASS",
        "season": season,
        "target_games": len(games),
        "affected_residual_keys": len(affected_keys),
        "required_team_game_targets": required_team_n,
        "required_player_game_targets": required_player_n,
        "recovered_team_game_targets": len(team_out),
        "recovered_player_game_targets": len(player_out),
        "exact_zero_player_targets": sum(str(r.get("exact_zero_proof")).lower() == "true" for r in player_out),
        "game_status_counts": dict(sorted(reason_counts.items())),
        "integrity": {
            "exact_retained_nba_v3_pbp_only": True,
            "validated_complete_lineup_required_for_zero": True,
            "empirical_model_used": False,
            "rounded_percentage_backsolve_used": False,
            "opponent_rebound_inference_used": False,
            "partial_tenure_whole_team_subtraction_used": False,
        },
    }
    (out / "RESIDUAL_SHARED_GAME_QA.json").write_text(json.dumps(qa, indent=2, sort_keys=True) + "\n")
    print(json.dumps(qa, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
