#!/usr/bin/env python3
"""Cross-validate modern CDN lineup reconstruction against solved 2024 legacy data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import modern_cdn_lineups as modern
import production_treb_engine_recovered as legacy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--cdn", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    nba = pd.read_csv(args.nba, low_memory=False)
    cdn = pd.read_csv(args.cdn, low_memory=False)
    nba["GAME_ID"] = pd.to_numeric(nba.GAME_ID, errors="raise").astype("int64")
    cdn["gameId"] = pd.to_numeric(cdn.gameId, errors="raise").astype("int64")

    nba_games = set(nba.GAME_ID.unique())
    cdn_games = set(cdn.gameId.unique())
    common = sorted(nba_games & cdn_games)
    legacy_failures = []
    modern_failures = []
    comparisons = []
    modern_repair_counts = []

    for n, game_id in enumerate(common, 1):
        ng = nba[nba.GAME_ID.eq(game_id)].copy()
        cg = cdn[cdn.gameId.eq(game_id)].copy()
        try:
            old = legacy.reconstruct_game_lineups(ng)
        except Exception as exc:
            legacy_failures.append({"game_id": int(game_id), "error": repr(exc)})
            continue
        try:
            new = modern.reconstruct_game_lineups(cg)
        except Exception as exc:
            modern_failures.append({"game_id": int(game_id), "error": repr(exc)})
            continue

        modern_repair_counts.append({"game_id": int(game_id), "audit_entries": len(new.repairs)})
        players = sorted(set(old.seconds) | set(new.seconds))
        for pid in players:
            a = float(old.seconds.get(pid, 0))
            b = float(new.seconds.get(pid, 0.0))
            comparisons.append({
                "game_id": int(game_id),
                "player_id": int(pid),
                "legacy_seconds": a,
                "modern_seconds": b,
                "delta_seconds": b - a,
                "abs_delta_seconds": abs(b - a),
            })
        if n % 100 == 0:
            print(f"validated {n}/{len(common)} games modern_failures={len(modern_failures)} legacy_failures={len(legacy_failures)}", flush=True)

    comp = pd.DataFrame(comparisons)
    if len(comp):
        max_delta = float(comp.abs_delta_seconds.max())
        within_001 = int(comp.abs_delta_seconds.le(0.01).sum())
        within_1 = int(comp.abs_delta_seconds.le(1.01).sum())
        within_2 = int(comp.abs_delta_seconds.le(2.01).sum())
        over_2 = comp[comp.abs_delta_seconds.gt(2.01)].sort_values("abs_delta_seconds", ascending=False)
        samples = over_2.head(100).to_dict("records")
    else:
        max_delta = None
        within_001 = within_1 = within_2 = 0
        samples = []

    payload = {
        "nba_games": len(nba_games),
        "cdn_games": len(cdn_games),
        "common_games": len(common),
        "legacy_failed_games": len(legacy_failures),
        "modern_failed_games": len(modern_failures),
        "comparison_player_game_rows": int(len(comp)),
        "seconds_within_0_01": within_001,
        "seconds_within_1_01": within_1,
        "seconds_within_2_01": within_2,
        "max_abs_delta_seconds": max_delta,
        "legacy_failures": legacy_failures[:100],
        "modern_failures": modern_failures[:200],
        "delta_over_2_seconds_samples": samples,
        "modern_audit_entries_total": int(sum(x["audit_entries"] for x in modern_repair_counts)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if not isinstance(v, list)}, indent=2))

    # This is a diagnostic gate, not an optimistic pass. Any modern failure or
    # player-game discrepancy beyond two seconds must be inspected before 2025 use.
    if modern_failures or (len(comp) and (comp.abs_delta_seconds > 2.01).any()):
        raise SystemExit("modern lineup bridge requires repair")
    print("MODERN_LINEUP_2024_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
