#!/usr/bin/env python3
"""Resolve legal historical lineup repair chains against independent CC0 minutes.

The CC0 PlayerStatistics snapshot stores historical `numMinutes` as completed
whole minutes.  This is empirically visible in already-solved games: exact
reconstructed times of N:00 through N:59.x are recorded as N.0, while DNP rows
are blank.  Therefore this audit uses an exact source-format rule rather than a
fuzzy tolerance:

- source blank/DNP -> reconstructed seconds must be zero;
- source whole minute N -> floor(reconstructed seconds / 60) must equal N;
- every reconstructed player with positive time must have a source game row.

Only full-game reconstructions that are already event/substitution legal enter
this audit.  A starter repair is recommended only when exactly one legal repair
chain satisfies the complete game-roster minute gate.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

AUDIT_VERSION = 3


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_blank_number(value: object) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except Exception:
        return False


def official_game_rows(cc0: dict) -> dict[int, dict[int, dict]]:
    games: dict[int, dict[int, dict]] = {}
    for row in cc0.get("blocker_rows", []):
        gid = int(row["normalized_game_id"])
        pid = int(float(row["personId"]))
        raw = row.get("parsed_minutes")
        blank = is_blank_number(raw)
        minutes = 0.0 if blank else float(raw)
        games.setdefault(gid, {})[pid] = {
            "source_minutes": minutes,
            "source_blank_dnp": blank,
            "player": " ".join(
                str(row.get(k) or "").strip() for k in ("firstName", "lastName")
            ).strip(),
            "team_id": int(float(row["playerteamId"])) if row.get("playerteamId") is not None else None,
        }
    return games


def score_seconds(player_seconds: dict, official: dict[int, dict]) -> dict:
    seconds = {int(pid): float(sec) for pid, sec in player_seconds.items()}
    comparisons = []
    violations = []

    for pid, src in sorted(official.items()):
        sec = max(0.0, float(seconds.get(pid, 0.0)))
        engine_minutes = sec / 60.0
        engine_completed_minutes = int(math.floor((sec + 1e-7) / 60.0))
        source_minutes = float(src["source_minutes"])
        source_completed_minutes = int(round(source_minutes))
        if src["source_blank_dnp"]:
            consistent = sec <= 1e-7
            rule = "blank_dnp_requires_zero_seconds"
        else:
            consistent = engine_completed_minutes == source_completed_minutes
            rule = "completed_minute_floor_match"
        rec = {
            "player_id": pid,
            "player": src.get("player"),
            "team_id": src.get("team_id"),
            "source_blank_dnp": bool(src["source_blank_dnp"]),
            "source_minutes": source_minutes,
            "engine_seconds": round(sec, 6),
            "engine_minutes": round(engine_minutes, 6),
            "engine_completed_minutes": engine_completed_minutes,
            "rule": rule,
            "consistent": bool(consistent),
        }
        comparisons.append(rec)
        if not consistent:
            violations.append(rec)

    missing_source_active = []
    for pid, sec in sorted(seconds.items()):
        if sec > 1e-7 and pid not in official:
            missing_source_active.append({
                "player_id": pid,
                "engine_seconds": round(sec, 6),
                "engine_minutes": round(sec / 60.0, 6),
            })

    floor_distance = 0
    for rec in violations:
        if rec["source_blank_dnp"]:
            floor_distance += max(1, rec["engine_completed_minutes"])
        else:
            floor_distance += abs(
                rec["engine_completed_minutes"] - int(round(rec["source_minutes"]))
            )

    return {
        "gate_pass": not violations and not missing_source_active and bool(comparisons),
        "source_roster_rows": len(official),
        "engine_active_players": sum(sec > 1e-7 for sec in seconds.values()),
        "minute_rule_violations": len(violations),
        "missing_source_active_players": len(missing_source_active),
        "floor_distance_minutes": int(floor_distance),
        "violations": violations,
        "missing_source_active": missing_source_active,
        "comparisons": comparisons,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary", type=Path, required=True)
    ap.add_argument("--cc0", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    canary = load(args.canary)
    cc0 = load(args.cc0)
    official_by_game = official_game_rows(cc0)

    # First demonstrate that the source-format rule holds on blocker games that
    # the lineup engine already solves without any ambiguity override.
    baseline = []
    for game in sorted(canary.get("games", []), key=lambda x: int(x["game_id"])):
        if game.get("status") not in {"PASS_V3_TEAM_LOCAL", "PASS_CDN"}:
            continue
        gid = int(game["game_id"])
        official = official_by_game.get(gid)
        if not official or not game.get("player_seconds"):
            continue
        baseline.append({
            "game_id": gid,
            "engine_status": game.get("status"),
            "score": score_seconds(game["player_seconds"], official),
        })

    rows = []
    for game in sorted(canary.get("games", []), key=lambda x: int(x["game_id"])):
        search = game.get("repair_search")
        if not isinstance(search, dict) or "full_game_solutions" not in search:
            continue
        gid = int(game["game_id"])
        official = official_by_game.get(gid)
        if not official:
            rows.append({
                "game_id": gid,
                "status": "NO_CC0_GAME_ROSTER",
                "recommended_repair_choices": None,
                "full_game_solution_count": len(search.get("full_game_solutions", [])),
            })
            continue

        scored = []
        for i, solution in enumerate(search.get("full_game_solutions", []), 1):
            score = score_seconds(solution.get("player_seconds", {}), official)
            scored.append({
                "solution_index": i,
                "repair_choices": solution.get("repair_choices", []),
                "score": score,
            })
        scored.sort(key=lambda x: (
            0 if x["score"]["gate_pass"] else 1,
            x["score"]["minute_rule_violations"] + x["score"]["missing_source_active_players"],
            x["score"]["floor_distance_minutes"],
            json.dumps(x["repair_choices"], sort_keys=True),
        ))
        passing = [x for x in scored if x["score"]["gate_pass"]]

        if len(passing) == 1:
            status = "UNIQUE_FLOOR_MINUTE_FIT"
            recommended = passing[0]["repair_choices"]
        elif len(passing) > 1:
            status = "MULTIPLE_FLOOR_MINUTE_FITS"
            recommended = None
        elif not scored:
            status = "NO_FULL_GAME_LEGAL_SOLUTION"
            recommended = None
        else:
            status = "NO_FLOOR_MINUTE_FIT"
            recommended = None

        rows.append({
            "game_id": gid,
            "status": status,
            "recommended_repair_choices": recommended,
            "search_complete": search.get("search_complete"),
            "states_explored": search.get("states_explored"),
            "full_game_solution_count": len(scored),
            "passing_solution_count": len(passing),
            "ranked_solutions": scored,
        })

    status_counts = {}
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    baseline_pass = sum(bool(x["score"]["gate_pass"]) for x in baseline)

    payload = {
        "status": "COMPLETE",
        "audit_version": AUDIT_VERSION,
        "source_minute_semantics": "whole_completed_minutes_floor; blank means DNP",
        "baseline_already_solved_games": len(baseline),
        "baseline_floor_gate_passes": baseline_pass,
        "baseline_floor_gate_failures": len(baseline) - baseline_pass,
        "games_with_repair_solutions": len(rows),
        "status_counts": status_counts,
        "auto_promoted_repairs": 0,
        "policy": "Unique floor-minute fits are recommendations only; explicit engine repairs are committed separately after source/event review.",
        "baseline": baseline,
        "games": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k not in {"baseline", "games"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
