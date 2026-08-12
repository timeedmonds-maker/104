#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import build_exact_game_fact_layer as base
import local_treb_rebuild as core
import production_treb_engine as rebound_engine
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io


def _norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def exact_identity_join(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha: int = 5) -> tuple[pd.DataFrame, dict]:
    """Baseline matcher first, then an exact-identity fallback for still-unmatched rebound rows.

    Pass 1 intentionally mirrors the current production join. Pass 2 may only
    claim an unused NBA EVENTMSGTYPE=4 event with either an identical normalized
    description or an identical player-name prefix plus cumulative (Off:N Def:M)
    counter. No fuzzy threshold is widened.
    """
    ordered_pbp = pbp_game.copy()
    ordered_pbp["PREV_PBP_DESCRIPTION"] = ordered_pbp.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    rebounds = ordered_pbp[ordered_pbp.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    rebounds["DESCRIPTION_NORM"] = rebounds.DESCRIPTION.map(_norm)
    rebounds["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.STARTTIME)]
    rebounds["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rebounds.PERIOD, rebounds.ENDTIME)]
    rows = list(rebounds.iterrows())
    nba = lineups.events
    game_id = int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0

    matches: list[int | None] = []
    ambiguous = 0
    manual = 0
    for _, row in rows:
        candidates = nba[(nba.PERIOD.eq(row.PERIOD)) &
                         (nba.ELAPSED.gt(row.START_ELAPSED - alpha)) &
                         (nba.ELAPSED.lt(row.END_ELAPSED + alpha))]
        scored = [(core._distance(row.DESCRIPTION_NORM, desc), int(ev), int(pos))
                  for pos, (ev, desc) in zip(candidates.index, zip(candidates.EVENTNUM, candidates.DESCRIPTION_NORM))]
        acceptable = [item for item in scored if item[0] < .2]
        if len(acceptable) > 1:
            ambiguous += 1
        if acceptable:
            matches.append(min(acceptable)[2])
            continue
        repair_event = rebound_engine.JOIN_REPAIRS.get((game_id, int(row.PERIOD), row.DESCRIPTION_NORM))
        if repair_event is not None:
            hit = nba[(nba.PERIOD.eq(row.PERIOD)) & nba.EVENTNUM.eq(repair_event)]
            if len(hit) == 1:
                matches.append(int(hit.index[0]))
                manual += 1
                continue
        matches.append(None)

    used = {int(index) for index in matches if index is not None}
    counter_re = re.compile(r"\(off:(\d+) def:(\d+)\)", re.I)

    def name_key(value: object) -> str:
        return _norm(value).split(" rebound", 1)[0].strip()

    exact_identity = 0
    exact_description = 0
    exact_player_counter = 0
    fallback_records: list[dict] = []
    for position, (_, row) in enumerate(rows):
        if matches[position] is not None:
            continue
        eligible = nba[(nba.PERIOD.eq(row.PERIOD)) & nba.EVENTMSGTYPE.eq(4) & ~nba.index.isin(used)]
        chosen: int | None = None
        method: str | None = None
        exact = eligible[eligible.DESCRIPTION_NORM.eq(row.DESCRIPTION_NORM)]
        if len(exact) == 1:
            chosen = int(exact.index[0])
            method = "exact_description"
        else:
            counter = counter_re.search(row.DESCRIPTION_NORM)
            if counter:
                counter_key = f"(off:{counter.group(1)} def:{counter.group(2)})"
                player_key = name_key(row.DESCRIPTION_NORM)
                hits = eligible[
                    eligible.DESCRIPTION_NORM.str.contains(re.escape(counter_key), regex=True) &
                    eligible.DESCRIPTION_NORM.map(name_key).eq(player_key)
                ]
                if len(hits) == 1:
                    chosen = int(hits.index[0])
                    method = "exact_player_counter"
        if chosen is not None:
            matches[position] = chosen
            used.add(chosen)
            exact_identity += 1
            exact_description += int(method == "exact_description")
            exact_player_counter += int(method == "exact_player_counter")
            fallback_records.append({
                "period": int(row.PERIOD),
                "pbp_description": str(row.DESCRIPTION),
                "nba_eventnum": int(nba.loc[chosen, "EVENTNUM"]),
                "nba_description": str(nba.loc[chosen, "DESCRIPTION"]),
                "method": method,
            })

    unmatched_rows: list[dict] = []
    for position, (_, row) in enumerate(rows):
        if matches[position] is None:
            unmatched_rows.append({
                "game_id": game_id,
                "period": int(row.PERIOD),
                "start_time": str(row.STARTTIME),
                "end_time": str(row.ENDTIME),
                "description": str(row.DESCRIPTION),
            })

    rebounds["NBA_INDEX"] = matches
    matched = rebounds[rebounds.NBA_INDEX.notna()].copy()
    matched["LINEUP"] = [nba.loc[int(i), "LINEUP"] for i in matched.NBA_INDEX]
    for column in ("EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PLAYER1_ID", "ELAPSED", "EVENTNUM"):
        matched["NBA_" + column] = [nba.loc[int(i), column] for i in matched.NBA_INDEX]
    matched["NBA_IS_REAL_REBOUND"] = [core._nba_real_rebound(nba, int(i)) for i in matched.NBA_INDEX]
    audit = {
        "total_pbp_rows": int(len(pbp_game)),
        "rebound_bearing_rows": int(len(rebounds)),
        "matched_rebound_bearing_rows": int(len(matched)),
        "unmatched_rebound_bearing_rows": int(len(unmatched_rows)),
        "ambiguous_matches": int(ambiguous),
        "manual_join_repairs": int(manual),
        "exact_identity_join_repairs": int(exact_identity),
        "exact_description_repairs": int(exact_description),
        "exact_player_counter_repairs": int(exact_player_counter),
        "exact_identity_records": fallback_records,
        "unmatched_rows": unmatched_rows,
    }
    return matched, audit


def canonical_rows(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--residual-json", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--chunk-index", type=int, required=True)
    ap.add_argument("--chunk-size", type=int, default=10)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    residual = json.loads(args.residual_json.read_text())
    target_ids = sorted({int(r["game_id"]) for r in residual if "unmatched PBP rebound rows" in str(r.get("error", ""))})
    start = args.chunk_index * args.chunk_size
    stop = min(len(target_ids), start + args.chunk_size)
    chunk_ids = target_ids[start:stop]

    nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}

    original_join = rebound_engine.join_pbp_rebounds
    results: list[dict] = []
    recovered = 0
    identity_repairs = 0
    exact_description = 0
    exact_player_counter = 0
    try:
        rebound_engine.join_pbp_rebounds = exact_identity_join
        for i, gid in enumerate(chunk_ids, 1):
            rec = {"game_id": gid}
            try:
                tr, pr, audit = base.build_game(gid, ng[gid], vg[gid], pg[gid])
                ja = audit["join_audit"]
                recovered += 1
                identity_repairs += int(ja.get("exact_identity_join_repairs", 0))
                exact_description += int(ja.get("exact_description_repairs", 0))
                exact_player_counter += int(ja.get("exact_player_counter_repairs", 0))
                rec.update({
                    "status": "RECOVERED",
                    "team_rows": len(tr),
                    "player_rows": len(pr),
                    "join_audit": ja,
                })
            except Exception as exc:
                rec.update({"status": "RESIDUAL", "error": f"{type(exc).__name__}: {exc}"})
            results.append(rec)
            print(f"EXACT_IDENTITY_DIAG year={args.year} chunk={args.chunk_index} game={i}/{len(chunk_ids)} gid={gid} status={rec['status']}", flush=True)
    finally:
        rebound_engine.join_pbp_rebounds = original_join

    controls: list[dict] = []
    control_regressions = 0
    if args.chunk_index == 0:
        common = sorted(set(ng) & set(vg) & set(pg) - set(target_ids))
        for gid in common:
            if len(controls) >= 2:
                break
            try:
                baseline_tr, baseline_pr, baseline_audit = base.build_game(gid, ng[gid], vg[gid], pg[gid])
            except Exception:
                continue
            try:
                rebound_engine.join_pbp_rebounds = exact_identity_join
                patched_tr, patched_pr, patched_audit = base.build_game(gid, ng[gid], vg[gid], pg[gid])
            finally:
                rebound_engine.join_pbp_rebounds = original_join
            same = canonical_rows(baseline_tr) == canonical_rows(patched_tr) and canonical_rows(baseline_pr) == canonical_rows(patched_pr)
            unexpected_fallbacks = int(patched_audit["join_audit"].get("exact_identity_join_repairs", 0))
            passed = bool(same and unexpected_fallbacks == 0)
            control_regressions += int(not passed)
            controls.append({
                "game_id": gid,
                "pass": passed,
                "team_player_rows_identical": bool(same),
                "exact_identity_join_repairs": unexpected_fallbacks,
            })

    payload = {
        "year": args.year,
        "season": f"{args.year}-{(args.year + 1) % 100:02d}",
        "chunk_index": args.chunk_index,
        "chunk_size": args.chunk_size,
        "season_target_count": len(target_ids),
        "slice_start": start,
        "slice_stop": stop,
        "target_game_ids": chunk_ids,
        "games_attempted": len(results),
        "recovered_games": recovered,
        "residual_games": len(results) - recovered,
        "exact_identity_join_repairs": identity_repairs,
        "exact_description_repairs": exact_description,
        "exact_player_counter_repairs": exact_player_counter,
        "healthy_controls": controls,
        "control_regressions": control_regressions,
        "games": results,
        "status": "CONTROL_REGRESSION" if control_regressions else "COMPLETE_DIAGNOSTIC",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({k: v for k, v in payload.items() if k != "games"}, indent=2), flush=True)
    return 3 if control_regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
