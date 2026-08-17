#!/usr/bin/env python3
"""Fail-closed period-local unique order-preserving rebound reconciliation candidate.

Starts from production_rebound_v4. It only repairs still-unmatched PBP rebound rows
when, within one period and the existing legal +/-5-second window, there is exactly
one order-preserving injection into unused NBA rebound events. NBA event reuse is
forbidden. No nearest-neighbour choice or approximate semantic guess is allowed.
"""
from __future__ import annotations
import re
import pandas as pd
import local_treb_rebuild as core
import production_rebound_v4 as base


def _norm(v: object) -> str:
    if pd.isna(v): return ""
    return re.sub(r"\s+", " ", str(v)).strip().lower()


def _pbp_rebounds(pbp_game: pd.DataFrame) -> pd.DataFrame:
    ordered = pbp_game.copy()
    ordered["PREV_PBP_DESCRIPTION"] = ordered.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    rows = ordered[ordered.DESCRIPTION.fillna("").str.contains("rebound", case=False)].copy()
    rows["DESCRIPTION_NORM"] = rows.DESCRIPTION.map(_norm)
    rows["START_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rows.PERIOD, rows.STARTTIME)]
    rows["END_ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(rows.PERIOD, rows.ENDTIME)]
    return rows


def _unique_order_assignment(rows: pd.DataFrame, nba: pd.DataFrame, used: set[int], alpha: int = 5):
    """Return unique mapping {pbp_index:nba_index}, or None if 0/>1 mappings.

    Uses iterative dynamic programming so uniqueness is evaluated exactly without
    Python recursion-depth dependence. Counts are capped at 2 because only
    zero/one/multiple solutions matter.
    """
    if rows.empty:
        return {}
    rr = rows.sort_values(["START_ELAPSED", "END_ELAPSED"], kind="stable")
    ev = nba[(nba.EVENTMSGTYPE.eq(4)) & (~nba.index.isin(used))].copy()
    ev = ev.sort_values(["ELAPSED", "EVENTNUM"], kind="stable")
    rlist = list(rr.iterrows())
    elist = [(int(i), int(r.ELAPSED)) for i, r in ev.iterrows()]
    n, m = len(rlist), len(elist)
    if m < n:
        return None
    allowed = []
    for _, r in rlist:
        lo = min(int(r.START_ELAPSED), int(r.END_ELAPSED)) - alpha
        hi = max(int(r.START_ELAPSED), int(r.END_ELAPSED)) + alpha
        allowed.append([lo < elapsed < hi for _, elapsed in elist])
    dp = [bytearray(m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        dp[n][j] = 1
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if (m - j) < (n - i):
                dp[i][j] = 0
                continue
            total = int(dp[i][j + 1])
            if allowed[i][j]:
                total += int(dp[i + 1][j + 1])
            dp[i][j] = 2 if total >= 2 else total
    if int(dp[0][0]) != 1:
        return None
    path = []
    i = j = 0
    while i < n:
        if j >= m:
            return None
        skip = int(dp[i][j + 1])
        match = int(dp[i + 1][j + 1]) if allowed[i][j] else 0
        if match and not skip:
            path.append(j); i += 1; j += 1
        elif skip and not match:
            j += 1
        else:
            return None
    if len(path) != n:
        return None
    return {int(rlist[k][0]): int(elist[path[k]][0]) for k in range(n)}


def join_pbp_rebounds(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha: int = 5):
    joined, audit = base.join_pbp_rebounds(lineups, pbp_game, alpha=alpha)
    if int(audit.get("unmatched_rebound_bearing_rows", 0)) == 0:
        audit = dict(audit); audit["period_unique_repairs"] = 0; audit["period_unique_records"] = []
        return joined, audit
    nba = lineups.events
    rebounds = _pbp_rebounds(pbp_game)
    used = set(pd.to_numeric(joined.NBA_INDEX, errors="coerce").dropna().astype(int)) if "NBA_INDEX" in joined.columns else set()
    matched_indices = set(joined.index)
    additions = []; records = []
    for period, period_rows in rebounds[~rebounds.index.isin(matched_indices)].groupby("PERIOD", sort=True):
        period_nba = nba[nba.PERIOD.eq(int(period))]
        mapping = _unique_order_assignment(period_rows, period_nba, used, alpha=alpha)
        if mapping is None:
            continue
        for pbp_idx, ni in mapping.items():
            if ni in used:
                raise ValueError(f"period-unique repair would reuse NBA event index={ni}")
            hit = period_rows.loc[[pbp_idx]].copy()
            hit["NBA_INDEX"] = ni
            hit["LINEUP"] = pd.Series([nba.loc[ni, "LINEUP"]], index=hit.index, dtype=object)
            for column in ("EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PLAYER1_ID", "ELAPSED", "EVENTNUM"):
                hit["NBA_" + column] = nba.loc[ni, column]
            hit["NBA_IS_REAL_REBOUND"] = bool(core._nba_real_rebound(nba, ni))
            additions.append(hit); used.add(ni); matched_indices.add(pbp_idx)
            records.append({"period": int(period), "pbp_index": int(pbp_idx), "start_time": str(rebounds.loc[pbp_idx, "STARTTIME"]), "end_time": str(rebounds.loc[pbp_idx, "ENDTIME"]), "pbp_description": str(rebounds.loc[pbp_idx, "DESCRIPTION"]), "nba_eventnum": int(nba.loc[ni, "EVENTNUM"]), "nba_elapsed": int(nba.loc[ni, "ELAPSED"]), "method": "unique_period_order_preserving_injection"})
    if additions:
        joined = pd.concat([joined, *additions], axis=0).sort_index(kind="stable")
    remaining = []
    joined_indices=set(joined.index)
    for idx, row in rebounds.iterrows():
        if idx not in joined_indices:
            remaining.append({"game_id": int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0, "period": int(row.PERIOD), "start_time": str(row.STARTTIME), "end_time": str(row.ENDTIME), "description": str(row.DESCRIPTION)})
    audit = dict(audit)
    audit["matched_rebound_bearing_rows"] = int(len(joined))
    audit["unmatched_rebound_bearing_rows"] = int(len(remaining))
    audit["unmatched_rows"] = remaining
    audit["period_unique_repairs"] = int(len(records))
    audit["period_unique_records"] = records
    return joined, audit


def classify_rebounds(pbp_game: pd.DataFrame):
    return base.classify_rebounds(pbp_game)
