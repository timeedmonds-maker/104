#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def norm_action(s: pd.Series) -> pd.Series:
    return s.astype("string").fillna("").str.strip().str.lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdn", type=Path, required=True)
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    cdn = pd.read_csv(args.cdn, low_memory=False)
    nba = pd.read_csv(args.nba, low_memory=False)
    for f in (cdn, nba):
        f["action_norm"] = norm_action(f["actionType"])
        f["gameId"] = pd.to_numeric(f["gameId"], errors="raise").astype("int64")
        f["actionNumber"] = pd.to_numeric(f["actionNumber"], errors="raise").astype("int64")

    c = cdn[cdn.action_norm.eq("rebound")].copy()
    n = nba[nba.action_norm.eq("rebound")].copy()
    if c.empty or n.empty:
        raise SystemExit("2025 rebound semantic gate failed: no rebound actions")

    ckeys = set(zip(c.gameId, c.actionNumber))
    nkeys = set(zip(n.gameId, n.actionNumber))
    shared = ckeys & nkeys
    cdn_only = ckeys - nkeys
    nba_only = nkeys - ckeys

    for col in ("personId", "reboundTotal", "reboundDefensiveTotal", "reboundOffensiveTotal", "teamId", "orderNumber"):
        c[col] = pd.to_numeric(c[col], errors="coerce")
    c = c.sort_values(["gameId", "personId", "orderNumber", "actionNumber"], kind="stable")
    player = c[c.personId.gt(0)].copy()
    grp = player.groupby(["gameId", "personId"], sort=False, dropna=False)
    for col in ("reboundTotal", "reboundDefensiveTotal", "reboundOffensiveTotal"):
        prev = grp[col].shift(1).fillna(0)
        player[f"delta_{col}"] = player[col] - prev

    complete = player[["reboundTotal", "reboundDefensiveTotal", "reboundOffensiveTotal"]].notna().all(axis=1)
    checked = player[complete].copy()
    checked["delta_component_sum"] = checked.delta_reboundDefensiveTotal + checked.delta_reboundOffensiveTotal
    semantic_fail = checked[
        ~checked.delta_reboundTotal.eq(1)
        | ~checked.delta_component_sum.eq(1)
        | ~checked.delta_reboundDefensiveTotal.isin([0, 1])
        | ~checked.delta_reboundOffensiveTotal.isin([0, 1])
    ]

    team = c[~c.personId.gt(0)].copy()
    samples = team[[x for x in ["gameId","actionNumber","clock","period","teamId","teamTricode","description","descriptor","subType","reboundTotal","reboundDefensiveTotal","reboundOffensiveTotal"] if x in team]].head(30).to_dict("records")

    payload = {
        "cdn_rebound_rows": int(len(c)),
        "nba_rebound_rows": int(len(n)),
        "shared_rebound_action_keys": int(len(shared)),
        "cdn_only_rebound_action_keys": int(len(cdn_only)),
        "nba_only_rebound_action_keys": int(len(nba_only)),
        "player_rebound_rows": int(len(player)),
        "player_rebound_rows_with_complete_counters": int(len(checked)),
        "player_counter_semantic_failures": int(len(semantic_fail)),
        "team_or_nonplayer_rebound_rows": int(len(team)),
        "team_or_nonplayer_samples": samples,
        "semantics": {
            "player_reboundTotal": "validated as cumulative player total iff failures=0",
            "player_reboundOffensiveTotal": "validated as cumulative offensive component iff failures=0",
            "player_reboundDefensiveTotal": "validated as cumulative defensive component iff failures=0",
            "team_rebounds": "not yet assumed live/real solely from actionType; must retain explicit dead-ball/period-end QA before production"
        }
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))

    # Strict semantic gate: the two source views must identify exactly the same rebound actions,
    # and every player rebound counter transition must be exactly one total rebound split O/D.
    if cdn_only or nba_only or len(semantic_fail) or len(checked) != len(player):
        raise SystemExit("2025 rebound semantic gate failed")
    print("TREB_2025_REBOUND_SEMANTICS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
