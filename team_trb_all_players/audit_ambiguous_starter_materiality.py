#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

import build_exact_game_fact_layer as base
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

THRESHOLD_PP_DEFAULT = 0.01
PLAYER_FIELDS = ["seconds_on", "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on"]
TEAM_FIELDS = ["game_seconds", "team_oreb", "team_dreb", "opponent_oreb", "opponent_dreb"]
AMBIG_TEXT = "non-unique v3/team-local starter solution"


class StarterAmbiguity(RuntimeError):
    def __init__(self, key, solutions, candidates, prior):
        super().__init__(f"starter ambiguity key={key} solutions={len(solutions)}")
        self.key = key
        self.solutions = solutions
        self.candidates = candidates
        self.prior = prior


_SELECTED_STARTERS = {}


def sid(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _materiality_choose_starters(period, game_id, period_number, team_id, player_team, prior, evidence_repairs):
    key = (int(game_id), int(period_number), int(team_id))
    explicit = lineup_engine.legacy.core.STARTER_REPAIRS.get(key)
    if explicit is not None:
        chosen = {int(x) for x in explicit}
        return chosen, {"type": "locked_starter_repair", "team_id": team_id, "period": period_number, "starters": sorted(chosen)}
    if key in evidence_repairs:
        chosen = set(evidence_repairs[key])
        return chosen, {"type": "evidence_starter_repair", "team_id": team_id, "period": period_number, "starters": sorted(chosen)}

    candidates = lineup_engine._candidate_starters(period, team_id, player_team)
    pool = set(candidates)
    if prior:
        pool |= set(prior)
    if len(pool) < 5:
        raise ValueError(f"unresolved v3/team-local starters game={game_id} period={period_number} team={team_id}: candidates={sorted(candidates)} prior={sorted(prior or [])}")

    combos = [set(c) for c in combinations(sorted(pool), 5) if candidates.issubset(c)] if len(candidates) < 5 else [set(c) for c in combinations(sorted(candidates), 5)]
    evaluated = []
    for combo in combos:
        legal, violations = lineup_engine._simulate_team(period, team_id, combo, player_team)
        if legal:
            evaluated.append((len(violations), tuple(sorted(combo)), violations))
    if not evaluated:
        raise ValueError(f"no legal v3/team-local starter solution game={game_id} period={period_number} team={team_id}: candidates={sorted(candidates)} prior={sorted(prior or [])}")
    evaluated.sort(key=lambda x: (x[0], x[1]))
    best_score = evaluated[0][0]
    best = [x for x in evaluated if x[0] == best_score]
    if len(best) != 1:
        solutions = [tuple(int(y) for y in x[1]) for x in best]
        selected = _SELECTED_STARTERS.get(key)
        if selected is None:
            raise StarterAmbiguity(key, solutions, sorted(candidates), sorted(prior or []))
        if selected not in solutions:
            raise ValueError(f"materiality-selected starter solution no longer legal key={key} selected={list(selected)}")
        chosen = set(selected)
        return chosen, {"type": "materiality_enumerated_starter_solution", "team_id": team_id, "period": period_number, "candidates": sorted(candidates), "prior": sorted(prior or []), "starters": sorted(chosen), "optimal_solution_count": len(solutions)}
    score, chosen_tuple, violations = best[0]
    if score:
        raise ValueError(f"starter solution requires missing in-period lineup transition game={game_id} period={period_number} team={team_id}: starters={list(chosen_tuple)} violations={violations[:10]}")
    chosen = set(chosen_tuple)
    return chosen, {"type": "v3_team_local_starter_solution", "team_id": team_id, "period": period_number, "candidates": sorted(candidates), "prior": sorted(prior or []), "starters": sorted(chosen)}


def _player_signature(rows):
    vals = []
    for r in rows:
        vals.append((int(r["team_id"]), sid(r["player_id"]), int(r["seconds_on"]), int(r["team_oreb_on"]), int(r["team_dreb_on"]), int(r["opponent_oreb_on"]), int(r["opponent_dreb_on"])))
    return tuple(sorted(vals))


def enumerate_game_variants(game_id, nba_game, v3_game, pbp_game, max_nodes, max_variants):
    global _SELECTED_STARTERS
    old_choose = lineup_engine._choose_starters
    lineup_engine._choose_starters = _materiality_choose_starters
    stack = [dict()]
    seen_assignments = set()
    variants_by_sig = {}
    dead_branches = []
    ambiguity_keys = {}
    nodes = 0
    capped = False
    try:
        while stack:
            choices = stack.pop()
            assign_sig = tuple(sorted((k, v) for k, v in choices.items()))
            if assign_sig in seen_assignments:
                continue
            seen_assignments.add(assign_sig)
            nodes += 1
            if nodes > max_nodes:
                capped = True
                break
            _SELECTED_STARTERS = dict(choices)
            try:
                tr, pr, audit = base.build_game(game_id, nba_game, v3_game, pbp_game)
                sig = _player_signature(pr)
                if sig not in variants_by_sig:
                    variants_by_sig[sig] = {"team_rows": tr, "player_rows": pr, "audit": audit, "choices": {"|".join(map(str, k)): list(v) for k, v in sorted(choices.items())}}
                    if len(variants_by_sig) > max_variants:
                        capped = True
                        break
            except StarterAmbiguity as exc:
                ambiguity_keys.setdefault(exc.key, set()).update(exc.solutions)
                for sol in reversed(exc.solutions):
                    nxt = dict(choices)
                    nxt[exc.key] = tuple(sol)
                    stack.append(nxt)
            except Exception as exc:
                dead_branches.append({"choices": {"|".join(map(str, k)): list(v) for k, v in sorted(choices.items())}, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        _SELECTED_STARTERS = {}
        lineup_engine._choose_starters = old_choose

    variants = list(variants_by_sig.values())
    team_invariant = True
    team_signature = None
    for v in variants:
        sig = tuple(sorted((int(r["team_id"]),) + tuple(int(r[x]) for x in TEAM_FIELDS) for r in v["team_rows"]))
        if team_signature is None:
            team_signature = sig
        elif sig != team_signature:
            team_invariant = False
    meta = {"game_id": int(game_id), "enumeration_nodes": nodes, "successful_distinct_variants": len(variants), "dead_branches": len(dead_branches), "capped": bool(capped), "team_totals_invariant": bool(team_invariant), "ambiguity_points": len(ambiguity_keys), "ambiguity_solution_counts": {"|".join(map(str, k)): len(v) for k, v in sorted(ambiguity_keys.items())}, "dead_branch_examples": dead_branches[:10]}
    return variants, meta


def ratio(team_reb, opp_reb):
    den = float(team_reb) + float(opp_reb)
    if den <= 0:
        raise ValueError(f"nonpositive rebound denominator team={team_reb} opp={opp_reb}")
    return float(team_reb) / den


def parse_teams(value):
    if pd.isna(value):
        return []
    return [int(x) for x in json.loads(str(value))]


def load_targets(path, season):
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if str(r.get("season")) != season or not bool(r.get("full_core_reuse")):
                continue
            r["team_id"] = int(r["team_id"])
            r["player_id"] = sid(r["player_id"])
            out.append(r)
    return out


def aggregate_baseline(team_df, player_df):
    team_df = team_df.copy(); player_df = player_df.copy()
    team_df["team_id"] = pd.to_numeric(team_df.team_id, errors="raise").astype("int64")
    player_df["team_id"] = pd.to_numeric(player_df.team_id, errors="raise").astype("int64")
    player_df["player_id"] = player_df.player_id.map(sid)
    tg = team_df.groupby("team_id", as_index=False)[TEAM_FIELDS].sum()
    pg = player_df.groupby(["team_id", "player_id"], as_index=False)[PLAYER_FIELDS].sum()
    team_map = {int(r.team_id): {f: int(round(float(getattr(r, f)))) for f in TEAM_FIELDS} for r in tg.itertuples(index=False)}
    player_map = {(int(r.team_id), sid(r.player_id)): {f: int(round(float(getattr(r, f)))) for f in PLAYER_FIELDS} for r in pg.itertuples(index=False)}
    return team_map, player_map


def contribution_signature(player_rows, team_id):
    vals = []
    for r in player_rows:
        if int(r["team_id"]) == int(team_id):
            vals.append((sid(r["player_id"]),) + tuple(int(r[f]) for f in PLAYER_FIELDS))
    return tuple(sorted(vals))


def signature_to_map(sig):
    return {str(x[0]): tuple(int(v) for v in x[1:]) for x in sig}


def combine_team_states(game_option_signatures, max_states):
    states = {tuple(): {}}
    capped = False
    for options in game_option_signatures:
        next_states = {}
        for state in states.values():
            for opt_sig in options:
                opt = signature_to_map(opt_sig)
                merged = dict(state)
                for pid, vals in opt.items():
                    prev = merged.get(pid, (0, 0, 0, 0, 0))
                    merged[pid] = tuple(prev[i] + vals[i] for i in range(5))
                sig = tuple(sorted((pid,) + tuple(vals) for pid, vals in merged.items()))
                next_states.setdefault(sig, merged)
                if len(next_states) > max_states:
                    capped = True
                    break
            if capped:
                break
        states = next_states
        if capped:
            break
    return list(states.values()), capped


def metric_values(team_total, player_total):
    team_on = float(player_total["team_oreb_on"] + player_total["team_dreb_on"])
    opp_on = float(player_total["opponent_oreb_on"] + player_total["opponent_dreb_on"])
    team_season = float(team_total["team_oreb"] + team_total["team_dreb"])
    opp_season = float(team_total["opponent_oreb"] + team_total["opponent_dreb"])
    team_off = team_season - team_on; opp_off = opp_season - opp_on
    if min(team_off, opp_off) < 0:
        raise ValueError(f"negative off rebound counts team_off={team_off} opp_off={opp_off}")
    on = ratio(team_on, opp_on); off = ratio(team_off, opp_off)
    return on, off, on - off


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True); ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--diagnostic-dir", type=Path, required=True); ap.add_argument("--baseline-dir", type=Path, required=True)
    ap.add_argument("--nba", type=Path, required=True); ap.add_argument("--v3", type=Path, required=True); ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True); ap.add_argument("--threshold-pp", type=float, default=THRESHOLD_PP_DEFAULT)
    ap.add_argument("--max-enumeration-nodes", type=int, default=20000); ap.add_argument("--max-game-variants", type=int, default=5000); ap.add_argument("--max-team-states", type=int, default=200000)
    args = ap.parse_args()

    season = f"{args.year}-{(args.year + 1) % 100:02d}"; outdir = args.output_dir; outdir.mkdir(parents=True, exist_ok=True)
    failures = pd.read_csv(args.diagnostic_dir / "ALL_GAME_FAILURES.csv", low_memory=False)
    failures = failures[failures.season.astype(str).eq(season)].copy()
    if failures.empty: raise RuntimeError(f"No diagnostic failure rows for {season}")
    failures["is_ambiguous"] = failures.error.fillna("").astype(str).str.contains(AMBIG_TEXT, regex=False)
    ambiguous = failures[failures.is_ambiguous].copy()
    if ambiguous.empty: raise RuntimeError(f"No ambiguous-starter games for {season}")

    team_failure_kinds = {}
    for r in failures.itertuples(index=False):
        for tid in parse_teams(r.teams_json): team_failure_kinds.setdefault(int(tid), []).append(bool(r.is_ambiguous))
    pure_teams = sorted(tid for tid, flags in team_failure_kinds.items() if flags and all(flags))
    targets = load_targets(args.targets, season); target_by_team = {}
    for r in targets: target_by_team.setdefault(int(r["team_id"]), []).append(r)
    pure_key_count = sum(len(target_by_team.get(tid, [])) for tid in pure_teams)

    nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False)); v3 = lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False)); pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}; vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}; pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}
    team_path = args.baseline_dir / "team_game_treb.csv.gz"; player_path = args.baseline_dir / "player_game_treb_on.csv.gz"
    if not team_path.is_file() or not player_path.is_file(): raise RuntimeError(f"Missing baseline exact facts in {args.baseline_dir}")
    baseline_team, baseline_player = aggregate_baseline(pd.read_csv(team_path, low_memory=False), pd.read_csv(player_path, low_memory=False))

    game_variants = {}; game_meta_rows = []
    for gid in sorted(pd.to_numeric(ambiguous.game_id, errors="raise").astype("int64").unique()):
        if gid not in ng or gid not in vg or gid not in pg: raise RuntimeError(f"Ambiguous game missing retained feed season={season} game={gid}")
        variants, meta = enumerate_game_variants(int(gid), ng[gid], vg[gid], pg[gid], args.max_enumeration_nodes, args.max_game_variants)
        game_variants[int(gid)] = variants; meta["season"] = season; game_meta_rows.append(meta)
        print(json.dumps({"event":"AMBIGUOUS_GAME_ENUMERATED", "season":season, "game_id":int(gid), "variants":meta["successful_distinct_variants"], "dead":meta["dead_branches"], "capped":meta["capped"], "team_totals_invariant":meta["team_totals_invariant"], "ambiguity_points":meta["ambiguity_points"]}), flush=True)
    pd.DataFrame(game_meta_rows).to_csv(outdir / "AMBIGUOUS_GAME_VARIANT_SUMMARY.csv", index=False)

    key_rows = []; team_rows = []
    for tid in pure_teams:
        ttargets = target_by_team.get(tid, [])
        if not ttargets: continue
        game_ids = sorted({int(r.game_id) for r in ambiguous.itertuples(index=False) if tid in parse_teams(r.teams_json)})
        unsafe_reason = ""; option_sigs = []; added_team_rows = []
        for gid in game_ids:
            variants = game_variants.get(gid, []); meta = next(x for x in game_meta_rows if int(x["game_id"]) == gid)
            if meta["capped"] or not meta["team_totals_invariant"] or not variants: unsafe_reason = f"game_{gid}_enumeration_not_complete"; break
            team_candidates = [r for r in variants[0]["team_rows"] if int(r["team_id"]) == tid]
            if len(team_candidates) != 1: unsafe_reason = f"game_{gid}_missing_team_row"; break
            added_team_rows.append(team_candidates[0]); opts = sorted(set(contribution_signature(v["player_rows"], tid) for v in variants)); option_sigs.append(opts or [tuple()])

        team_base = dict(baseline_team.get(tid, {f: 0 for f in TEAM_FIELDS}))
        if not unsafe_reason:
            for r in added_team_rows:
                for f in TEAM_FIELDS: team_base[f] = int(team_base.get(f, 0)) + int(r[f])
        states = []; compatible = []; states_capped = False
        if not unsafe_reason:
            states, states_capped = combine_team_states(option_sigs, args.max_team_states)
            if states_capped: unsafe_reason = "team_state_enumeration_cap"
            elif not states: unsafe_reason = "no_team_states"
        if not unsafe_reason:
            for st in states:
                ok = True
                for t in ttargets:
                    pid = sid(t["player_id"]); base_vals = baseline_player.get((tid, pid), {f: 0 for f in PLAYER_FIELDS}); add = st.get(pid, (0,0,0,0,0))
                    actual_seconds = int(base_vals["seconds_on"]) + int(add[0]); target_seconds = float(t.get("seconds_on", 0.0))
                    if abs(float(actual_seconds)-target_seconds) > 60.0: ok = False; break
                if ok: compatible.append(st)
            if not compatible: unsafe_reason = "no_state_satisfies_all_target_minutes_within_60s"

        team_max_range = 0.0; accepted_keys = 0
        if not unsafe_reason:
            for t in ttargets:
                pid = sid(t["player_id"]); base_vals = baseline_player.get((tid, pid), {f: 0 for f in PLAYER_FIELDS}); vals = []; seconds_vals = []
                for st in compatible:
                    add = st.get(pid, (0,0,0,0,0)); ptotal = {f: int(base_vals.get(f,0)) + int(add[i]) for i,f in enumerate(PLAYER_FIELDS)}
                    on, off, swing = metric_values(team_base, ptotal); vals.append((on,off,swing)); seconds_vals.append(ptotal["seconds_on"])
                on_vals=[x[0] for x in vals]; off_vals=[x[1] for x in vals]; swing_vals=[x[2] for x in vals]
                on_range_pp=(max(on_vals)-min(on_vals))*100.0; off_range_pp=(max(off_vals)-min(off_vals))*100.0; swing_range_pp=(max(swing_vals)-min(swing_vals))*100.0
                max_range_pp=max(on_range_pp,off_range_pp,swing_range_pp); accepted=bool(max_range_pp <= args.threshold_pp + 1e-12)
                if accepted: accepted_keys += 1
                team_max_range=max(team_max_range,max_range_pp)
                key_rows.append({"season":season,"team_id":tid,"player_id":pid,"player":t.get("player",""),"ambiguous_games":len(game_ids),"game_ids":"|".join(str(x) for x in game_ids),"coherent_states":len(states),"target_minute_compatible_states":len(compatible),"target_seconds":float(t.get("seconds_on",0.0)),"seconds_min":min(seconds_vals),"seconds_max":max(seconds_vals),"treb_on_min":min(on_vals),"treb_on_max":max(on_vals),"treb_on_range_pp":on_range_pp,"treb_off_min":min(off_vals),"treb_off_max":max(off_vals),"treb_off_range_pp":off_range_pp,"treb_swing_min":min(swing_vals),"treb_swing_max":max(swing_vals),"treb_swing_range_pp":swing_range_pp,"max_range_pp":max_range_pp,"threshold_pp":args.threshold_pp,"accepted_immaterial":accepted,"status":"ACCEPT_IMMATERIAL" if accepted else "MATERIAL_REPAIR_REQUIRED"})
        else:
            for t in ttargets: key_rows.append({"season":season,"team_id":tid,"player_id":sid(t["player_id"]),"player":t.get("player",""),"ambiguous_games":len(game_ids),"game_ids":"|".join(str(x) for x in game_ids),"threshold_pp":args.threshold_pp,"accepted_immaterial":False,"status":"AUDIT_UNRESOLVED","error":unsafe_reason})
        team_rows.append({"season":season,"team_id":tid,"target_keys":len(ttargets),"ambiguous_games":len(game_ids),"game_ids":"|".join(str(x) for x in game_ids),"coherent_states":len(states),"target_minute_compatible_states":len(compatible),"accepted_keys":accepted_keys,"max_range_pp":team_max_range if not unsafe_reason else None,"status":"ACCEPT_IMMATERIAL" if not unsafe_reason and accepted_keys==len(ttargets) else "MATERIAL_REPAIR_REQUIRED" if not unsafe_reason else "AUDIT_UNRESOLVED","error":unsafe_reason})

    key_df=pd.DataFrame(key_rows); team_out=pd.DataFrame(team_rows); key_df.to_csv(outdir/"STARTER_MATERIALITY_KEYS.csv",index=False); team_out.to_csv(outdir/"STARTER_MATERIALITY_TEAM_SEASONS.csv",index=False)
    counts=key_df.status.value_counts().to_dict() if len(key_df) else {}
    gate={"status":"PASS","season":season,"threshold_pp":args.threshold_pp,"ambiguous_games":int(len(ambiguous)),"pure_ambiguity_team_seasons":int(len(pure_teams)),"pure_ambiguity_target_keys":int(pure_key_count),"audited_key_rows":int(len(key_df)),"accepted_immaterial_keys":int(counts.get("ACCEPT_IMMATERIAL",0)),"material_repair_required_keys":int(counts.get("MATERIAL_REPAIR_REQUIRED",0)),"audit_unresolved_keys":int(counts.get("AUDIT_UNRESOLVED",0)),"accepted_immaterial_team_seasons":int(team_out.status.eq("ACCEPT_IMMATERIAL").sum()) if len(team_out) else 0,"material_team_seasons":int(team_out.status.eq("MATERIAL_REPAIR_REQUIRED").sum()) if len(team_out) else 0,"unresolved_team_seasons":int(team_out.status.eq("AUDIT_UNRESOLVED").sum()) if len(team_out) else 0,"max_observed_range_pp":float(pd.to_numeric(key_df.get("max_range_pp"),errors="coerce").max()) if len(key_df) and "max_range_pp" in key_df else None,"minutes_gate_tolerance_seconds":60.0,"materiality_policy":"coherent starter variants satisfying established target-minute gate; max TREB ON/OFF/SWING range <= 0.01 percentage points","rounded_percentage_backsolve_used":False,"opponent_rebound_inference_used":False,"partial_tenure_whole_team_subtraction_used":False}
    (outdir/"SEASON_STARTER_MATERIALITY_GATE.json").write_text(json.dumps(gate,indent=2)+"\n",encoding="utf-8"); print(json.dumps(gate,indent=2),flush=True); return 0


if __name__ == "__main__": raise SystemExit(main())
