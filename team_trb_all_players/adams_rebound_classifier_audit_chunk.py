#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import run_local_treb_production as io
import production_treb_engine as rebound_engine
import production_treb_engine_v3 as lineup_engine
import local_treb_rebuild as core

TEAM = 1610612760
ADAMS = 203500
API = "https://api.pbpstats.com/get-game-stats"
LOCKED = {
    "seconds_on": 143368,
    "team_oreb_on": 816,
    "team_dreb_on": 1846,
    "opponent_oreb_on": 643,
    "opponent_dreb_on": 1632,
}
REPAIR_KEY = (21600270, 5, TEAM)
REPAIR = [201566, 201627, 203460, 203506, 203924]
MODES = ("current", "real_first", "description_counter")


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=6,
        connect=6,
        read=6,
        status=6,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch(sess: requests.Session, gid: int, typ: str) -> dict:
    r = sess.get(API, params={"GameId": f"{gid:010d}", "Type": typ}, timeout=90)
    r.raise_for_status()
    return r.json()


def side(d: dict) -> str:
    if int(d["home_team_id"]) == TEAM:
        return "Home"
    if int(d["away_team_id"]) == TEAM:
        return "Away"
    raise RuntimeError(f"OKC missing API game {d.get('home_team_id')} {d.get('away_team_id')}")


def rowmap(d: dict, s: str) -> dict[str, dict]:
    return {str(r["EntityId"]): r for r in d["stats"][s]["FullGame"]}


def n(row: dict, key: str) -> int:
    return int(round(float(row.get(key, 0) or 0)))


def secs(value: object) -> int:
    s = str(value or "0:00")
    if ":" in s:
        a, b = s.split(":", 1)
        return int(a) * 60 + int(round(float(b)))
    return int(round(float(s) * 60))


def aggregate(joined: pd.DataFrame, mode: str) -> dict[str, int]:
    j = joined.copy()
    real = j.IS_REAL_REBOUND.astype(bool)
    if mode == "current":
        off = j.IS_OREB.astype(bool)
    elif mode == "real_first":
        real_num = real.astype(int).groupby([j[c] for c in core.POSSESSION_ID], dropna=False).cumsum()
        poss_oreb = j.groupby(core.POSSESSION_ID, dropna=False).OFFENSIVEREBOUNDS.transform("first")
        off = real & real_num.le(poss_oreb)
    elif mode == "description_counter":
        off = pd.Series(False, index=j.index)
        resolved = pd.Series(False, index=j.index)
        last: dict[int, tuple[int, int]] = {}
        for idx, row in j.iterrows():
            if not bool(real.loc[idx]):
                continue
            m = re.search(r"\(Off:(\d+) Def:(\d+)\)", str(row.DESCRIPTION), re.I)
            pid = int(row.NBA_PLAYER1_ID) if pd.notna(row.NBA_PLAYER1_ID) else 0
            if not m or pid <= 0:
                continue
            o, d = map(int, m.groups())
            po, pd_ = last.get(pid, (0, 0))
            do, dd = o - po, d - pd_
            if do == 1 and dd == 0:
                off.loc[idx] = True
                resolved.loc[idx] = True
            elif dd == 1 and do == 0:
                off.loc[idx] = False
                resolved.loc[idx] = True
            last[pid] = (o, d)
        j["_DESC_RESOLVED"] = resolved
    else:
        raise ValueError(mode)

    on = j.LINEUP.map(lambda x: ADAMS in x)
    okc_off = ~j.OPPONENT.astype(str).eq("OKC")
    okc_def = ~okc_off
    out = {
        "team_oreb_on": int((on & okc_off & real & off).sum()),
        "team_dreb_on": int((on & okc_def & real & ~off).sum()),
        "opponent_oreb_on": int((on & okc_def & real & off).sum()),
        "opponent_dreb_on": int((on & okc_off & real & ~off).sum()),
    }
    if mode == "description_counter":
        out["description_unresolved_real_on"] = int((on & real & ~j["_DESC_RESOLVED"]).sum())
    return out


def blank_totals() -> dict[str, int]:
    return {
        "seconds_on": 0,
        "team_oreb_on": 0,
        "team_dreb_on": 0,
        "opponent_oreb_on": 0,
        "opponent_dreb_on": 0,
    }


def diffs(obs: dict[str, int], ref: dict[str, int]) -> dict[str, int]:
    return {k: int(obs[k] - ref[k]) for k in ref}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--chunk-index", type=int, required=True)
    ap.add_argument("--chunk-size", type=int, default=10)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    previous_repair = core.STARTER_REPAIRS.get(REPAIR_KEY)
    core.STARTER_REPAIRS[REPAIR_KEY] = REPAIR
    try:
        nba = io.normalize_nba(pd.read_csv(args.nba, low_memory=False))
        v3 = lineup_engine.normalize_v3(pd.read_csv(args.v3, low_memory=False))
        pbp = io.normalize_pbp(pd.read_csv(args.pbp, low_memory=False))
        ng = {int(g): f.copy() for g, f in nba.groupby("GAME_ID", sort=False)}
        vg = {int(g): f.copy() for g, f in v3.groupby("gameId", sort=False)}
        pg = {int(g): f.copy() for g, f in pbp.groupby("GAMEID", sort=False)}
        okc_ids = sorted(int(g) for g in pbp.loc[pbp.OPPONENT.astype(str).eq("OKC"), "GAMEID"].unique())
        start = args.chunk_index * args.chunk_size
        stop = min(len(okc_ids), start + args.chunk_size)
        chunk_ids = okc_ids[start:stop]

        sess = session()
        totals = {m: blank_totals() for m in MODES}
        truth = blank_totals()
        per_game: list[dict] = []
        failures: list[dict] = []
        universe = 0

        for i, gid in enumerate(chunk_ids, 1):
            if gid not in ng or gid not in vg or gid not in pg:
                failures.append({"game_id": gid, "error": "missing local source"})
                continue
            try:
                lu = lineup_engine.reconstruct_game_lineups(ng[gid], vg[gid])
                joined, audit = rebound_engine.join_pbp_rebounds(lu, pg[gid])
                if audit.get("unmatched_rebound_bearing_rows", 0):
                    raise RuntimeError(f"unmatched={audit['unmatched_rebound_bearing_rows']}")
                joined = rebound_engine.classify_rebounds(joined)
                poss = pg[gid].drop_duplicates(core.POSSESSION_ID)
                universe += int(poss.loc[~poss.OPPONENT.astype(str).eq("OKC"), "OFFENSIVEREBOUNDS"].sum())

                local: dict[str, dict[str, int]] = {}
                for mode in MODES:
                    vals = aggregate(joined, mode)
                    vals["seconds_on"] = int(lu.seconds.get(ADAMS, 0))
                    local[mode] = vals
                    for k in totals[mode]:
                        totals[mode][k] += int(vals.get(k, 0))

                line = fetch(sess, gid, "Lineup")
                opp = fetch(sess, gid, "LineupOpponent")
                s = side(line)
                lm, om = rowmap(line, s), rowmap(opp, s)
                if set(lm) != set(om):
                    raise RuntimeError("Lineup/LineupOpponent entity mismatch")
                t = blank_totals()
                for eid, r in lm.items():
                    if str(ADAMS) not in eid.split("-"):
                        continue
                    o = om[eid]
                    t["seconds_on"] += secs(r.get("Minutes"))
                    t["team_oreb_on"] += n(r, "OffRebounds")
                    t["team_dreb_on"] += n(r, "DefRebounds")
                    t["opponent_oreb_on"] += n(o, "OffRebounds")
                    t["opponent_dreb_on"] += n(o, "DefRebounds")
                for k in truth:
                    truth[k] += t[k]
                per_game.append({"game_id": gid, "truth": t, "local": local, "join_audit": audit})
                print(
                    f"ADAMS_CLASSIFIER_CHUNK chunk={args.chunk_index} game={i}/{len(chunk_ids)} "
                    f"gid={gid} seconds={t['seconds_on']}",
                    flush=True,
                )
                time.sleep(0.12)
            except Exception as exc:
                failures.append({"game_id": gid, "error": f"{type(exc).__name__}: {exc}"})

        method_summary: dict[str, dict] = {}
        for mode, observed in totals.items():
            mae = {k: 0 for k in truth}
            exact = 0
            for game in per_game:
                ds = diffs(game["local"][mode], game["truth"])
                if all(x == 0 for x in ds.values()):
                    exact += 1
                for k, value in ds.items():
                    mae[k] += abs(value)
            method_summary[mode] = {
                "observed": observed,
                "exact_games": exact,
                "sum_abs_game_error": mae,
            }

        payload = {
            "season": "2016-17",
            "chunk_index": args.chunk_index,
            "chunk_size": args.chunk_size,
            "slice_start": start,
            "slice_stop": stop,
            "okc_games_total": len(okc_ids),
            "game_ids": chunk_ids,
            "games_expected_in_chunk": len(chunk_ids),
            "games_compared": len(per_game),
            "failures": failures,
            "okc_oreb_universe": universe,
            "api_truth": truth,
            "methods": method_summary,
            "per_game": per_game,
            "explicit_test_repair": {
                "game_id": 21600270,
                "period": 5,
                "team_id": TEAM,
                "starters": REPAIR,
            },
            "status": "COMPLETE" if len(per_game) == len(chunk_ids) and not failures else "PARTIAL",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in payload.items() if k != "per_game"}, indent=2), flush=True)
        return 0 if payload["status"] == "COMPLETE" else 2
    finally:
        if previous_repair is None:
            core.STARTER_REPAIRS.pop(REPAIR_KEY, None)
        else:
            core.STARTER_REPAIRS[REPAIR_KEY] = previous_repair


if __name__ == "__main__":
    raise SystemExit(main())
