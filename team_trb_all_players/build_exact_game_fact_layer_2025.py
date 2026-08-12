#!/usr/bin/env python3
"""Build 2025-26 exact per-game TREB facts from the static NBA CDN archive."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import modern_cdn_lineups as modern
import run_modern_treb_production_2025 as prod
import run_local_treb_production as base

SEASON = "2025-26"


def _team_abbr(game: pd.DataFrame) -> dict[int, str]:
    out = {}
    for tid, rows in game.dropna(subset=["teamId"]).groupby("teamId"):
        vals = [str(x).strip() for x in rows.get("teamTricode", pd.Series(dtype=str)).dropna() if str(x).strip()]
        if vals:
            out[int(tid)] = max(set(vals), key=vals.count)
    return out


def _player_names(game: pd.DataFrame) -> dict[int, str]:
    out = {}
    if "personId" not in game or "playerName" not in game:
        return out
    for pid, rows in game.dropna(subset=["personId"]).groupby("personId"):
        vals = [str(x).strip() for x in rows.playerName.dropna() if str(x).strip()]
        if vals and 0 < int(pid) < modern.PLAYER_MAX:
            out[int(pid)] = max(set(vals), key=vals.count)
    return out


def _count(mask: pd.Series) -> int:
    return int(mask.fillna(False).sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdn", type=Path, required=True)
    ap.add_argument("--schedule", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cdn = prod.normalize_cdn(pd.read_csv(args.cdn, low_memory=False))
    schedule = [r for r in base.read_jsonl_gz(args.schedule) if r.get("season") == SEASON]
    dates = {int(r["game_id"]): str(r["game_date"]) for r in schedule}
    schedule_ids = set(dates)
    groups = {int(gid): frame.copy() for gid, frame in cdn.groupby("gameId", sort=False)}
    source_ids = set(groups)
    game_ids = sorted(schedule_ids & source_ids)
    del cdn

    team_rows = []
    player_rows = []
    audits = []
    failures = []

    for i, gid in enumerate(game_ids, 1):
        game = groups[gid]
        try:
            lineup = modern.reconstruct_game_lineups(game)
            rebounds = prod.classify_game_rebounds(lineup.events, lineup.teams)
            duration = prod.game_duration_seconds(game)
            player_team = modern._player_team_map(game)
            abbr = _team_abbr(game)
            names = _player_names(game)
            real = rebounds.IS_REAL_REBOUND.astype(bool) if not rebounds.empty else pd.Series(dtype=bool)
            oreb = rebounds.IS_OREB.astype(bool) if not rebounds.empty else pd.Series(dtype=bool)
            game_team_rows = []
            game_player_rows = []

            for tid in lineup.teams:
                own = rebounds.REBOUND_TEAM_ID.astype("int64").eq(int(tid)) if not rebounds.empty else pd.Series(dtype=bool)
                game_team_rows.append({
                    "game_id": int(gid), "game_date": dates[gid], "team_id": int(tid),
                    "team_abbr": abbr.get(int(tid), ""), "game_seconds": int(duration),
                    "team_oreb": _count(own & oreb) if not rebounds.empty else 0,
                    "team_dreb": _count(own & real & ~oreb) if not rebounds.empty else 0,
                    "opponent_oreb": _count(~own & oreb) if not rebounds.empty else 0,
                    "opponent_dreb": _count(~own & real & ~oreb) if not rebounds.empty else 0,
                })

            for pid, seconds in sorted(lineup.seconds.items()):
                pid = int(pid); sec = int(round(float(seconds)))
                if sec <= 0:
                    continue
                tid = player_team.get(pid)
                if tid is None:
                    raise ValueError(f"positive-second player has no team mapping game={gid} player={pid}")
                tid = int(tid)
                if rebounds.empty:
                    on = own = pd.Series(dtype=bool)
                else:
                    on = rebounds.LINEUP.map(lambda x: pid in x)
                    own = rebounds.REBOUND_TEAM_ID.astype("int64").eq(tid)
                game_player_rows.append({
                    "game_id": int(gid), "game_date": dates[gid], "team_id": tid,
                    "team_abbr": abbr.get(tid, ""), "player_id": pid, "player": names.get(pid, ""),
                    "seconds_on": sec,
                    "team_oreb_on": _count(on & own & oreb) if not rebounds.empty else 0,
                    "team_dreb_on": _count(on & own & real & ~oreb) if not rebounds.empty else 0,
                    "opponent_oreb_on": _count(on & ~own & oreb) if not rebounds.empty else 0,
                    "opponent_dreb_on": _count(on & ~own & real & ~oreb) if not rebounds.empty else 0,
                })

            for tid in lineup.teams:
                observed = sum(r["seconds_on"] for r in game_player_rows if r["team_id"] == int(tid))
                expected = duration * 5
                if observed != expected:
                    raise ValueError(f"team player-seconds mismatch game={gid} team={tid}: {observed} != {expected}")
            team_rows.extend(game_team_rows)
            player_rows.extend(game_player_rows)
            audits.append({"game_id": int(gid), "repairs": lineup.repairs, "duration_seconds": int(duration)})
        except Exception as exc:
            failures.append({"game_id": int(gid), "error": f"{type(exc).__name__}: {exc}"})
        if i % 50 == 0 or i == len(game_ids):
            print(f"GAME_FACT_2025_PROGRESS {i}/{len(game_ids)} success={len(audits)} failures={len(failures)}", flush=True)

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    team_df.to_csv(args.output_dir / "team_game_treb.csv.gz", index=False, compression="gzip")
    player_df.to_csv(args.output_dir / "player_game_treb_on.csv.gz", index=False, compression="gzip")
    (args.output_dir / "game_audit.json").write_text(json.dumps(audits, indent=2) + "\n")
    (args.output_dir / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")
    qa = {
        "season": SEASON,
        "schedule_games": len(schedule_ids),
        "source_games": len(source_ids),
        "common_games": len(game_ids),
        "successful_games": len(audits),
        "failed_games": len(failures),
        "team_game_rows": len(team_df),
        "player_game_rows": len(player_df),
        "status": "PASS" if not failures and schedule_ids.issubset(source_ids) else "INCOMPLETE",
    }
    (args.output_dir / "qa.json").write_text(json.dumps(qa, indent=2) + "\n")
    print(json.dumps(qa, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
