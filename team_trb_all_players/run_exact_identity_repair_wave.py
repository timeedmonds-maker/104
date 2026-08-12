#!/usr/bin/env python3
"""Rerun only Wave-1 unmatched-rebound residual games with the validated exact join.

This intentionally monkeypatches only the rebound join used by the exact game
fact builder. Lineup reconstruction, rebound classification, arithmetic, and
all other strict guards remain unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_exact_game_fact_layer as base
import exact_identity_rebound_join as exact_join
import production_treb_engine_v3 as engine
import run_local_treb_production as io


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--nba", required=True)
    ap.add_argument("--v3", required=True)
    ap.add_argument("--pbp", required=True)
    ap.add_argument("--residual", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--patch-sha", default="")
    args = ap.parse_args()

    year = args.year
    season = f"{year}-{(year + 1) % 100:02d}"
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    residual = json.loads(Path(args.residual).read_text(encoding="utf-8"))
    target_ids = sorted(
        {
            int(row["game_id"])
            for row in residual
            if "unmatched PBP rebound rows" in str(row.get("error", ""))
        }
    )

    nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3 = engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}

    # Narrow override validated by canary run 31578961858.
    engine.join_pbp_rebounds = exact_join.join_pbp_rebounds

    team_rows: list[dict] = []
    player_rows: list[dict] = []
    audits: list[dict] = []
    failures: list[dict] = []

    for i, gid in enumerate(target_ids, 1):
        try:
            if gid not in ng or gid not in vg or gid not in pg:
                raise ValueError(
                    f"missing source layer nba={gid in ng} v3={gid in vg} pbp={gid in pg}"
                )
            tr, pr, audit = base.build_game(gid, ng[gid], vg[gid], pg[gid])
            team_rows.extend(tr)
            player_rows.extend(pr)
            audits.append(audit)
        except Exception as exc:
            failures.append(
                {
                    "season": season,
                    "game_id": gid,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if i % 10 == 0 or i == len(target_ids):
            print(
                f"WAVE2 season={season} processed={i}/{len(target_ids)} "
                f"recovered={len(audits)} residual={len(failures)}",
                flush=True,
            )

    pd.DataFrame(team_rows).to_csv(out / "team_game_treb.csv.gz", index=False, compression="gzip")
    pd.DataFrame(player_rows).to_csv(out / "player_game_treb_on.csv.gz", index=False, compression="gzip")
    (out / "game_audit.json").write_text(json.dumps(audits, indent=2) + "\n", encoding="utf-8")
    (out / "residual_failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")

    qa = {
        "year": year,
        "season": season,
        "patch_sha": args.patch_sha,
        "target_games": len(target_ids),
        "recovered_games": len(audits),
        "residual_failed_games": len(failures),
        "exact_identity_join_repairs": sum(
            int(a["join_audit"].get("exact_identity_join_repairs", 0)) for a in audits
        ),
        "recovered_team_rows": len(team_rows),
        "recovered_player_rows": len(player_rows),
        "status": "PASS" if not failures else "REPAIR_REQUIRED",
    }
    (out / "qa.json").write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(qa, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
