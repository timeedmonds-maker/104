#!/usr/bin/env python3
"""Audit strict team-rebound event identity, including bracket-format PBP rows.

Diagnostic only.  Resolve a PBP team rebound to a team ID using source-native
identifiers only:
  * bracket prefix, e.g. [HOU], via NBA v3 teamTricode -> teamId;
  * otherwise the exact normalized team-rebound name used by legacy NBA Stats.
Then require exactly one unused *team/placeholder rebound event* for that team
inside the PBP row's legal time interval.

No fuzzy team-name aliases and no possession-row ordering are used.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v4 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

BRACKET_RE = re.compile(r"^\s*\[([A-Za-z]{2,4})\]\s*")


def norm(v: object) -> str:
    return re.sub(r"\s+", " ", "" if pd.isna(v) else str(v)).strip().lower()


def name_key(v: object) -> str:
    s = norm(v).split(" rebound", 1)[0].strip()
    # Legacy NBA team rows sometimes spell the prefix as 'X team'.  Keep an
    # exact secondary alias with that terminal source word removed.
    return re.sub(r"\s+team$", "", s).strip()


def make_rows(pbp_game: pd.DataFrame) -> pd.DataFrame:
    r = pbp_game[pbp_game.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    r["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.STARTTIME)]
    r["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(r.PERIOD, r.ENDTIME)]
    return r


def tricode_map(v3_game: pd.DataFrame) -> dict[str, int]:
    out: dict[str, set[int]] = {}
    if "teamTricode" not in v3_game.columns or "teamId" not in v3_game.columns:
        return {}
    for _, r in v3_game.dropna(subset=["teamTricode", "teamId"]).iterrows():
        tid = int(r.teamId)
        tri = str(r.teamTricode).strip().upper()
        if tid > 0 and tri:
            out.setdefault(tri, set()).add(tid)
    return {k: next(iter(v)) for k, v in out.items() if len(v) == 1}


def nba_team_rebound_id(row: pd.Series) -> int | None:
    pid = int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
    if 0 < pid < core.PLAYER_MAX:
        return None
    tid = int(row.PLAYER1_TEAM_ID) if "PLAYER1_TEAM_ID" in row.index and pd.notna(row.PLAYER1_TEAM_ID) else 0
    if tid > 0:
        return tid
    if pid >= core.PLAYER_MAX:
        return pid
    return None


def exact_team_name_map(events: pd.DataFrame) -> dict[str, int]:
    d: dict[str, set[int]] = {}
    for _, r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        tid = nba_team_rebound_id(r)
        if tid is None:
            continue
        key = name_key(r.DESCRIPTION_NORM)
        if key:
            d.setdefault(key, set()).add(int(tid))
    return {k: next(iter(v)) for k, v in d.items() if len(v) == 1}


def resolve_team(description: object, tri: dict[str, int], exact_names: dict[str, int]):
    text = str(description)
    m = BRACKET_RE.match(text)
    if m:
        code = m.group(1).upper()
        return tri.get(code), "bracket_tricode" if code in tri else None
    key = name_key(text)
    if key in exact_names:
        return exact_names[key], "exact_legacy_team_name"
    return None, None


def candidates(events: pd.DataFrame, row: pd.Series, team_id: int, alpha: int = 5) -> list[int]:
    lo = min(int(row.START_ELAPSED), int(row.END_ELAPSED)) - alpha
    hi = max(int(row.START_ELAPSED), int(row.END_ELAPSED)) + alpha
    span = events[
        events.PERIOD.eq(int(row.PERIOD))
        & events.EVENTMSGTYPE.eq(4)
        & events.ELAPSED.gt(lo)
        & events.ELAPSED.lt(hi)
    ]
    out = []
    for idx, r in span.iterrows():
        if nba_team_rebound_id(r) == int(team_id):
            out.append(int(idx))
    return out


def event_record(events: pd.DataFrame, idx: int) -> dict:
    r = events.loc[idx]
    return {
        "nba_index": int(idx),
        "eventnum": int(r.EVENTNUM),
        "elapsed": int(r.ELAPSED),
        "player1_id": int(r.PLAYER1_ID),
        "player1_team_id": int(r.PLAYER1_TEAM_ID) if "PLAYER1_TEAM_ID" in r.index else 0,
        "description": str(r.DESCRIPTION_NORM),
        "lineup": [int(x) for x in r.LINEUP],
        "real": bool(core._nba_real_rebound(events, int(idx))),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--chunk-id", required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    ids = [int(x) for x in a.games.split(",") if x]
    nba = io.normalize_nba(pd.read_csv(a.nba, low_memory=False))
    v3 = lineup_engine.normalize_v3(pd.read_csv(a.v3, low_memory=False))
    pbp = io.normalize_pbp(pd.read_csv(a.pbp, low_memory=False))
    ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
    vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
    pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}

    c = {
        "resolved_controls": 0,
        "bracket_controls": 0,
        "legacy_name_controls": 0,
        "unique_candidate_controls": 0,
        "identity_correct": 0,
        "identity_wrong": 0,
        "zero_candidate_controls": 0,
        "multiple_candidate_controls": 0,
        "resolved_residual_rows": 0,
        "bracket_residual_rows": 0,
        "legacy_name_residual_rows": 0,
        "unique_candidate_residual_rows": 0,
        "unique_unused_candidate_residual_rows": 0,
        "candidate_real_true": 0,
        "candidate_real_false": 0,
    }
    wrong = []
    residual = []

    for gid in ids:
        if gid not in ng or gid not in vg or gid not in pg:
            raise KeyError(f"missing source game={gid}")
        lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
        events = lu.events
        joined, _ = rebound.join_pbp_rebounds(lu, pg[gid])
        rows = make_rows(pg[gid])
        tri = tricode_map(vg[gid])
        exact_names = exact_team_name_map(events)
        used = {int(x) for x in pd.to_numeric(joined.NBA_INDEX, errors="coerce").dropna().astype(int)}

        for idx, row in rows.iterrows():
            tid, method = resolve_team(row.DESCRIPTION, tri, exact_names)
            matched = idx in joined.index and pd.notna(joined.loc[idx, "NBA_INDEX"])
            if matched:
                if tid is None:
                    continue
                actual = int(joined.loc[idx, "NBA_INDEX"])
                # Only team/placeholder rebound controls validate this rule.
                actual_tid = nba_team_rebound_id(events.loc[actual]) if int(events.loc[actual, "EVENTMSGTYPE"]) == 4 else None
                if actual_tid is None:
                    continue
                c["resolved_controls"] += 1
                c["bracket_controls" if method == "bracket_tricode" else "legacy_name_controls"] += 1
                cand = candidates(events, row, int(tid))
                if len(cand) == 0:
                    c["zero_candidate_controls"] += 1
                    continue
                if len(cand) != 1:
                    c["multiple_candidate_controls"] += 1
                    continue
                c["unique_candidate_controls"] += 1
                pred = cand[0]
                if pred == actual:
                    c["identity_correct"] += 1
                else:
                    c["identity_wrong"] += 1
                    wrong.append({
                        "game_id": gid, "pbp_index": int(idx), "description": str(row.DESCRIPTION),
                        "resolved_team_id": int(tid), "method": method,
                        "actual": event_record(events, actual), "predicted": event_record(events, pred),
                    })
                continue

            if idx in joined.index:
                continue
            rec = {
                "game_id": gid, "pbp_index": int(idx), "period": int(row.PERIOD),
                "start_time": str(row.STARTTIME), "end_time": str(row.ENDTIME),
                "description": str(row.DESCRIPTION), "resolved_team_id": None if tid is None else int(tid),
                "method": method, "candidate_count": 0, "candidate_unused": False, "candidate": None,
            }
            if tid is not None:
                c["resolved_residual_rows"] += 1
                c["bracket_residual_rows" if method == "bracket_tricode" else "legacy_name_residual_rows"] += 1
                cand = candidates(events, row, int(tid))
                rec["candidate_count"] = len(cand)
                if len(cand) == 1:
                    c["unique_candidate_residual_rows"] += 1
                    ni = cand[0]
                    rec["candidate_unused"] = ni not in used
                    rec["candidate"] = event_record(events, ni)
                    if rec["candidate_unused"]:
                        c["unique_unused_candidate_residual_rows"] += 1
                        if rec["candidate"]["real"]: c["candidate_real_true"] += 1
                        else: c["candidate_real_false"] += 1
            residual.append(rec)

    out = {"status": "DIAGNOSTIC_ONLY", "chunk_id": a.chunk_id, "year": a.year,
           "controls": c, "wrong_records": wrong, "residual_rows": residual}
    a.output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"chunk_id": a.chunk_id, "year": a.year, "controls": c}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
