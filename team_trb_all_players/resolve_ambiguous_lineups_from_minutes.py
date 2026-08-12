#!/usr/bin/env python3
"""Resolve explicit historical lineup trials against independent CC0 minutes.

This script never invents a lineup.  It consumes only:
1) full-game legal reconstruction trials emitted by canary_v3_lineup_repair.py;
2) independent per-game player minutes from the CC0 PlayerStatistics dataset.

Old box scores often expose whole-minute values, so a trial is considered a
bounded minute fit when every candidate player's reconstructed time is within
0.55 minutes of the source value.  Blank source minutes are treated as zero for
players who have an explicit row in the game roster.  A repair is recommended
only when exactly one full-game trial satisfies that bounded condition.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROUNDING_TOLERANCE_MINUTES = 0.55


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_minutes(evidence: dict) -> dict[int, float]:
    out: dict[int, float] = {}
    for row in evidence.get("candidate_rows", []):
        raw = row.get("personId")
        if raw is None:
            continue
        pid = int(float(raw))
        value = row.get("parsed_minutes")
        out[pid] = 0.0 if value is None else float(value)
    return out


def score_trial(trial: dict, official: dict[int, float], candidate_ids: list[int]) -> dict:
    reconstructed = {int(pid): float(sec) / 60.0 for pid, sec in trial.get("candidate_seconds", {}).items()}
    comparisons = []
    missing_official = []
    for pid in candidate_ids:
        if pid not in official:
            missing_official.append(pid)
            continue
        engine = reconstructed.get(pid, 0.0)
        source = official[pid]
        delta = abs(engine - source)
        comparisons.append({
            "player_id": pid,
            "engine_minutes": round(engine, 6),
            "source_minutes": source,
            "abs_delta_minutes": round(delta, 6),
            "within_rounding_bound": delta <= ROUNDING_TOLERANCE_MINUTES,
        })
    out_of_bound = sum(not x["within_rounding_bound"] for x in comparisons)
    interval_penalty = sum(max(0.0, x["abs_delta_minutes"] - ROUNDING_TOLERANCE_MINUTES) for x in comparisons)
    return {
        "starters": trial.get("starters", []),
        "comparisons": comparisons,
        "missing_official_candidate_rows": missing_official,
        "out_of_bound_candidates": out_of_bound,
        "interval_penalty_minutes": round(interval_penalty, 6),
        "sum_abs_delta_minutes": round(sum(x["abs_delta_minutes"] for x in comparisons), 6),
        "max_abs_delta_minutes": round(max((x["abs_delta_minutes"] for x in comparisons), default=0.0), 6),
        "bounded_fit": bool(comparisons) and not missing_official and out_of_bound == 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary", type=Path, required=True)
    ap.add_argument("--cc0", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    canary = load(args.canary)
    cc0 = load(args.cc0)
    cc0_by_game = {int(x["game_id"]): x for x in cc0.get("ambiguity_evidence", [])}
    rows = []

    for game in sorted(canary.get("games", []), key=lambda x: int(x["game_id"])):
        trials = game.get("explicit_starter_trials")
        if not trials:
            continue
        gid = int(game["game_id"])
        evidence = cc0_by_game.get(gid)
        if evidence is None:
            rows.append({"game_id": gid, "status": "NO_CC0_EVIDENCE", "recommended_starters": None})
            continue
        candidate_ids = sorted({int(x) for x in evidence.get("candidate_ids", [])})
        official = source_minutes(evidence)
        scored = [score_trial(t, official, candidate_ids) for t in trials.get("full_game_successes", [])]
        scored.sort(key=lambda x: (x["out_of_bound_candidates"], x["interval_penalty_minutes"], x["sum_abs_delta_minutes"], x["starters"]))
        bounded = [x for x in scored if x["bounded_fit"]]
        if len(bounded) == 1:
            status = "UNIQUE_BOUNDED_MINUTE_FIT"
            recommended = bounded[0]["starters"]
        elif len(bounded) > 1:
            status = "MULTIPLE_BOUNDED_MINUTE_FITS"
            recommended = None
        elif not scored:
            status = "NO_FULL_GAME_LEGAL_TRIAL"
            recommended = None
        else:
            status = "NO_BOUNDED_MINUTE_FIT"
            recommended = None
        rows.append({
            "game_id": gid,
            "period": trials.get("period"),
            "team_id": trials.get("team_id"),
            "status": status,
            "recommended_starters": recommended,
            "source_minutes": {str(k): v for k, v in sorted(official.items())},
            "trials_attempted": trials.get("trials_attempted"),
            "full_game_success_count": len(scored),
            "failed_trial_count": len(trials.get("full_game_failures", [])),
            "ranked_trials": scored,
        })

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    payload = {
        "status": "COMPLETE",
        "rounding_tolerance_minutes": ROUNDING_TOLERANCE_MINUTES,
        "games_with_explicit_trials": len(rows),
        "status_counts": counts,
        "auto_promoted_repairs": 0,
        "policy": "Recommendations are evidence only. Engine repair files are updated separately after unique bounded fit and event-legality review.",
        "games": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "games"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
