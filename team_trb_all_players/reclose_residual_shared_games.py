#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import pathlib

REQP = ("seconds_on", "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on")
REQT = ("team_oreb", "team_dreb", "opponent_oreb", "opponent_dreb")
FULL_CORE_BASE_EXACT = 8397
FULL_CORE_TARGET = 9647


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


def load_accepted(*roots: pathlib.Path):
    rows = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*MATERIALITY_ACCEPTED*.csv"):
            try:
                with open(path, newline="") as f:
                    for r in csv.DictReader(f):
                        if not r.get("season") or not r.get("team_id") or not r.get("player_id"):
                            continue
                        k = key(r)
                        if k in seen:
                            continue
                        seen.add(k); rows.append(r)
            except Exception:
                continue
    return rows, seen


def inject_player_file(facts, path: pathlib.Path, label: str) -> int:
    n = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            k = (gid(r["game_id"]), tid(r["team_id"]), pid(r["player_id"]))
            vals = {z: float(r[z]) for z in REQP}
            old = facts.get(k, {})
            for z, v in vals.items():
                if z in old and abs(old[z] - v) > 1e-9:
                    raise RuntimeError(f"CONFLICT_PLAYER {label} {k} {z}: {old[z]} vs {v}")
                facts[k][z] = v
            n += 1
    return n


def inject_team_file(facts, path: pathlib.Path, label: str) -> int:
    n = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            k = (gid(r["game_id"]), tid(r["team_id"]), "")
            vals = {z: float(r[z]) for z in REQT}
            old = facts.get(k, {})
            for z, v in vals.items():
                if z in old and abs(old[z] - v) > 1e-9:
                    raise RuntimeError(f"CONFLICT_TEAM {label} {k} {z}: {old[z]} vs {v}")
                facts[k][z] = v
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current-dir", type=pathlib.Path, required=True)
    ap.add_argument("--tenure-dir", type=pathlib.Path, required=True)
    ap.add_argument("--consensus-dir", type=pathlib.Path, required=True)
    ap.add_argument("--pbp-dir", type=pathlib.Path, required=True)
    ap.add_argument("--recovered-old-dir", type=pathlib.Path, required=True)
    ap.add_argument("--recovered-mid-dir", type=pathlib.Path, required=True)
    ap.add_argument("--recovered-new-dir", type=pathlib.Path, required=True)
    ap.add_argument("--shared-dir", type=pathlib.Path, required=True)
    ap.add_argument("--materiality-dir", type=pathlib.Path, required=True)
    ap.add_argument("--materiality-2015-dir", type=pathlib.Path, required=True)
    ap.add_argument("--targets", type=pathlib.Path, required=True)
    ap.add_argument("--ledger", type=pathlib.Path, required=True)
    ap.add_argument("--output-dir", type=pathlib.Path, required=True)
    ap.add_argument("--minutes-gate-seconds", type=float, default=60.0)
    args = ap.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    current = list(csv.DictReader(open(find_one(args.current_dir, "AUTONOMOUS_BLOCKER_MANIFEST.csv"), newline="")))
    prior = list(csv.DictReader(open(find_one(args.current_dir, "TREB_CUMULATIVE_EXACT_PROMOTED.csv"), newline="")))
    current_by = {key(r): r for r in current}
    prior_keys = {key(r) for r in prior}
    if len(current_by) != len(current):
        raise RuntimeError("duplicate current blocker keys")
    if len(prior_keys) != len(prior):
        raise RuntimeError("duplicate prior exact promotion keys")

    materiality_rows, materiality_keys = load_accepted(args.materiality_dir, args.materiality_2015_dir)
    materiality_keys &= set(current_by)
    materiality_rows = [r for r in materiality_rows if key(r) in materiality_keys]
    candidate_keys = set(current_by) - materiality_keys

    targets = {}
    with gzip.open(args.targets, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            k = key(r)
            if k in candidate_keys:
                targets[k] = r
    if set(targets) != candidate_keys:
        missing = sorted(candidate_keys - set(targets))
        raise RuntimeError(f"missing canonical targets: {len(missing)} {missing[:5]}")

    # Retained schedule-audited exact identities take precedence.
    tenure_games = {}
    tenure_source = {}
    schedule_path = find_one(args.tenure_dir, "TREB_949_RETAINED_SCHEDULE_AUDITED_TENURE_AUDIT.csv")
    with open(schedule_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "EXACT_RETAINED_SCHEDULE_AUDITED_TENURE_IDENTITY":
                continue
            k = key(r)
            if k not in candidate_keys:
                continue
            games = {gid(x) for x in str(r.get("game_ids") or "").split("|") if str(x).strip()}
            expected = int(float(r.get("expected_team_games") or 0))
            if len(games) != expected:
                raise RuntimeError(f"schedule identity count mismatch {k}: {len(games)} vs {expected}")
            tenure_games[k] = games
            tenure_source[k] = "schedule_audited_tenure_identity"

    # Exact V3 roster-ledger game identity is a settled fallback for keys not covered by schedule audit.
    ledger_games = collections.defaultdict(set)
    ledger_seconds = {}
    with gzip.open(args.ledger, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                k = key(r)
            except Exception:
                continue
            if k not in candidate_keys:
                continue
            g = gid(r["game_id"])
            ledger_games[k].add(g)
            ledger_seconds[(k, g)] = float(r.get("seconds_game") or 0)
    for k in sorted(candidate_keys - set(tenure_games)):
        expected = int(float(targets[k].get("team_games_in_tenure") or 0))
        gs = set(ledger_games.get(k, set()))
        if len(gs) == expected and (gs or expected == 0):
            tenure_games[k] = gs
            tenure_source[k] = "exact_v3_roster_ledger_tenure_identity"

    facts = collections.defaultdict(dict)
    consensus_path = find_one(args.consensus_dir, "PROMOTABLE_RETAINED_FACT_CONSENSUS.csv")
    with open(consensus_path, newline="") as f:
        for r in csv.DictReader(f):
            facts[(gid(r["game_id"]), tid(r["team_id"]), pid(r.get("player_id", "")))][r["field"]] = float(r["value"])

    injected = {}
    injected["old"] = inject_player_file(facts, find_one(args.recovered_old_dir, "RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz"), "old")
    injected["mid"] = inject_player_file(facts, find_one(args.recovered_mid_dir, "RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz"), "mid")
    injected["new"] = inject_player_file(facts, find_one(args.recovered_new_dir, "RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz"), "new")

    shared_team_files = list(args.shared_dir.rglob("RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz"))
    shared_player_files = list(args.shared_dir.rglob("RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz"))
    shared_team_n = 0
    shared_player_n = 0
    for p in shared_team_files:
        shared_team_n += inject_team_file(facts, p, f"shared:{p}")
    for p in shared_player_files:
        shared_player_n += inject_player_file(facts, p, f"shared:{p}")

    overrides = {}
    pbp_path = find_one(args.pbp_dir, "TREB_143_V2_PBP_EXACT_AUDIT.csv")
    with open(pbp_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "PASS_EXACT" or str(r.get("validation_pass")).strip().lower() not in {"true", "1"}:
                continue
            k = (gid(r["game_id"]), tid(r["team_id"]))
            v = {z: float(r[z]) for z in REQT}
            if k in overrides and any(abs(overrides[k][z] - v[z]) > 1e-9 for z in REQT):
                raise RuntimeError(f"CONFLICT_PBP_TEAM {k}")
            overrides[k] = v

    def team_fact(g, t):
        cv = facts.get((g, t, ""), {})
        if all(z in cv for z in REQT):
            return {z: cv[z] for z in REQT}
        return overrides.get((g, t))

    promoted = []
    diagnostics = []
    reasons = collections.Counter()

    for k in sorted(candidate_keys):
        if k not in tenure_games:
            reasons["NO_EXACT_TENURE_IDENTITY"] += 1
            diagnostics.append({
                "season": k[0], "team_id": k[1], "player_id": k[2],
                "status": "NO_EXACT_TENURE_IDENTITY", "minutes_delta_seconds": "",
                "missing_team_count": 0, "missing_player_count": 0, "bad_count": 0,
                "detail": json.dumps({"missing_team": [], "missing_player": [], "bad": []}, separators=(",", ":")),
                "tenure_identity_source": "",
            })
            continue

        t = targets[k]
        games = tenure_games[k]
        expected_games = int(float(t.get("team_games_in_tenure") or 0))
        if len(games) != expected_games:
            raise RuntimeError(f"tenure game count mismatch {k}: {len(games)} vs {expected_games}")
        agg = collections.Counter()
        mt, mp, bad = [], [], []
        zero = 0
        for g in sorted(games):
            tv = team_fact(g, k[1])
            if tv is None:
                mt.append(g); continue
            pv = facts.get((g, k[1], k[2]), {})
            if not all(z in pv for z in REQP):
                sec = ledger_seconds.get((k, g))
                if sec is not None and abs(sec) <= 1e-9:
                    pv = {z: 0.0 for z in REQP}; zero += 1
                else:
                    mp.append(g); continue
            comps = [
                tv["team_oreb"] - pv["team_oreb_on"],
                tv["team_dreb"] - pv["team_dreb_on"],
                tv["opponent_oreb"] - pv["opponent_oreb_on"],
                tv["opponent_dreb"] - pv["opponent_dreb_on"],
            ]
            if min(comps) < -1e-9:
                bad.append(g); continue
            for z in REQP:
                agg[z] += pv[z]
            for z in REQT:
                agg[z] += tv[z]

        delta = abs(agg["seconds_on"] - float(t.get("seconds_on") or 0))
        if mt or mp or bad or delta > args.minutes_gate_seconds:
            status = "BLOCKED_MISSING_PRIMITIVES" if (mt or mp) else "BLOCKED_VALIDATION"
            reasons[status] += 1
            diagnostics.append({
                "season": k[0], "team_id": k[1], "player_id": k[2], "status": status,
                "minutes_delta_seconds": delta, "missing_team_count": len(mt), "missing_player_count": len(mp),
                "bad_count": len(bad),
                "detail": json.dumps({"missing_team": mt, "missing_player": mp, "bad": bad}, separators=(",", ":")),
                "tenure_identity_source": tenure_source[k],
            })
            continue

        tr_on = agg["team_oreb_on"] + agg["team_dreb_on"]
        op_on = agg["opponent_oreb_on"] + agg["opponent_dreb_on"]
        tr = agg["team_oreb"] + agg["team_dreb"]
        op = agg["opponent_oreb"] + agg["opponent_dreb"]
        tr_off = tr - tr_on
        op_off = op - op_on
        if min(tr_on, op_on, tr_off, op_off) < -1e-9 or tr_on + op_on <= 0 or tr_off + op_off <= 0:
            reasons["BLOCKED_DENOMINATOR_OR_NEGATIVE"] += 1
            diagnostics.append({
                "season": k[0], "team_id": k[1], "player_id": k[2], "status": "BLOCKED_DENOMINATOR_OR_NEGATIVE",
                "minutes_delta_seconds": delta, "missing_team_count": 0, "missing_player_count": 0, "bad_count": 0,
                "detail": json.dumps({"missing_team": [], "missing_player": [], "bad": []}, separators=(",", ":")),
                "tenure_identity_source": tenure_source[k],
            })
            continue

        on = 100.0 * tr_on / (tr_on + op_on)
        off = 100.0 * tr_off / (tr_off + op_off)
        promoted.append({
            "season": k[0], "team_id": k[1], "player_id": k[2], "metric": "TotalReboundPct",
            "on": on, "off_corrected": off, "on_minus_off_corrected": on - off,
            "seconds_on": agg["seconds_on"], "team_games_in_tenure": len(games),
            "provenance": f"exact {tenure_source[k]} + exact retained consensus/recovered/shared-game primitives",
        })
        reasons["PROMOTED_EXACT"] += 1

    new_keys = {key(r) for r in promoted}
    if new_keys & prior_keys:
        raise RuntimeError("new exact promotions overlap prior cumulative exact promotions")
    if new_keys & materiality_keys:
        raise RuntimeError("new exact promotions overlap excluded materiality keys")

    cumulative = prior + promoted
    remain = [r for r in current if key(r) not in new_keys and key(r) not in materiality_keys]
    exact_full_core = FULL_CORE_BASE_EXACT + len(cumulative)
    resolved_full_core = exact_full_core + len(materiality_keys)
    if resolved_full_core + len(remain) != FULL_CORE_TARGET:
        raise RuntimeError(f"accounting failure: {resolved_full_core}+{len(remain)} != {FULL_CORE_TARGET}")

    def write_csv(path, rows, fallback_fields):
        fields = sorted({z for r in rows for z in r}) if rows else list(fallback_fields)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    write_csv(out / "TREB_NEW_EXACT_PROMOTED.csv", promoted, ["season", "team_id", "player_id", "metric", "on", "off_corrected", "on_minus_off_corrected"])
    write_csv(out / "TREB_CUMULATIVE_EXACT_PROMOTED.csv", cumulative, ["season", "team_id", "player_id", "metric", "on", "off_corrected", "on_minus_off_corrected"])
    write_csv(out / "TREB_MATERIALITY_ACCEPTED.csv", materiality_rows, ["season", "team_id", "player_id", "metric", "on", "off_corrected", "on_minus_off_corrected"])
    write_csv(out / "AUTONOMOUS_BLOCKER_MANIFEST.csv", remain, ["season", "team_id", "player_id"])
    write_csv(out / "TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv", diagnostics, ["season", "team_id", "player_id", "status", "minutes_delta_seconds", "missing_team_count", "missing_player_count", "bad_count", "detail", "tenure_identity_source"])

    # Emit next shared-game registry for autonomous continuation.
    game_registry = collections.defaultdict(lambda: {"team_targets": set(), "player_targets": set(), "keys": set()})
    for r in diagnostics:
        if r["status"] != "BLOCKED_MISSING_PRIMITIVES":
            continue
        k = (str(r["season"]), tid(r["team_id"]), pid(r["player_id"]))
        d = json.loads(r.get("detail") or "{}")
        for g in d.get("missing_team", []):
            x = game_registry[(k[0], gid(g))]; x["team_targets"].add(k[1]); x["keys"].add(k)
        for g in d.get("missing_player", []):
            x = game_registry[(k[0], gid(g))]; x["player_targets"].add((k[1], k[2])); x["keys"].add(k)
    registry_rows = []
    for (season, game), v in sorted(game_registry.items()):
        registry_rows.append({
            "season": season, "game_id": game,
            "team_target_count": len(v["team_targets"]), "player_target_count": len(v["player_targets"]),
            "affected_key_count": len(v["keys"]),
            "team_ids": "|".join(sorted(v["team_targets"])),
            "player_targets": "|".join(f"{t}:{p}" for t, p in sorted(v["player_targets"])),
        })
    write_csv(out / "NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv", registry_rows, ["season", "game_id", "team_target_count", "player_target_count", "affected_key_count", "team_ids", "player_targets"])

    qa = {
        "status": "PASS",
        "starting_exact_full_core": FULL_CORE_BASE_EXACT + len(prior),
        "starting_blocker_manifest": len(current),
        "materiality_accepted_keys": len(materiality_keys),
        "candidate_keys_reclosed": len(candidate_keys),
        "exact_tenure_identity_keys": len(tenure_games),
        "schedule_identity_keys": sum(v == "schedule_audited_tenure_identity" for v in tenure_source.values()),
        "v3_ledger_identity_keys": sum(v == "exact_v3_roster_ledger_tenure_identity" for v in tenure_source.values()),
        "shared_team_facts_injected": shared_team_n,
        "shared_player_facts_injected": shared_player_n,
        "prior_recovered_player_facts": injected,
        "new_exact_promotions": len(promoted),
        "cumulative_exact_promotions_from_original_1250": len(cumulative),
        "ending_exact_full_core": exact_full_core,
        "ending_production_resolved_full_core": resolved_full_core,
        "ending_residual": len(remain),
        "next_shared_games": len(registry_rows),
        "reason_counts": dict(sorted(reasons.items())),
        "ready_for_final_assembly": resolved_full_core == FULL_CORE_TARGET and not remain,
        "integrity": {
            "exact_tenure_identity_required": True,
            "exact_raw_counts_only_for_exact_rows": True,
            "materiality_rows_separately_tagged": True,
            "minutes_gate_seconds": args.minutes_gate_seconds,
            "empirical_model_used": False,
            "rounded_percentage_backsolve_used": False,
            "opponent_rebound_inference_used": False,
            "partial_tenure_whole_team_subtraction_used": False,
        },
        "next": "if next_shared_games > 0, recover only those games; concurrently target validation/tenure exceptions; if ready_for_final_assembly, launch final 90-metric assembly",
    }
    (out / "TREB_SHARED_GAME_RECLOSURE_QA.json").write_text(json.dumps(qa, indent=2, sort_keys=True) + "\n")
    print(json.dumps(qa, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
