#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

import pandas as pd


COUNTER_RE = re.compile(r"\(\s*off\s*:\s*(\d+)\s+def\s*:\s*(\d+)\s*\)", re.I)
TEAM_ID_MIN = 1_610_612_737


def norm_action(s: pd.Series) -> pd.Series:
    return s.astype("string").fillna("").str.strip().str.lower()


def records_for_keys(df: pd.DataFrame, keys: set[tuple[int, int]]) -> list[dict]:
    if not keys:
        return []
    key_index = pd.MultiIndex.from_arrays([df.gameId, df.actionNumber])
    wanted = pd.MultiIndex.from_tuples(sorted(keys))
    rows = df[key_index.isin(wanted)].copy()
    cols = [
        x for x in [
            "gameId", "actionNumber", "orderNumber", "clock", "period", "actionType",
            "subType", "descriptor", "description", "personId", "playerName", "teamId",
            "teamTricode", "reboundTotal", "reboundDefensiveTotal", "reboundOffensiveTotal",
            "shotResult", "possession", "personIdsFilter",
        ] if x in rows
    ]
    out = rows[cols].sort_values(["gameId", "actionNumber"], kind="stable")
    return out.where(pd.notna(out), None).to_dict("records")


def parse_description_counters(value: object) -> tuple[int | None, int | None]:
    if pd.isna(value):
        return None, None
    m = COUNTER_RE.search(str(value))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def player_person_id(row: pd.Series) -> int | None:
    pid = pd.to_numeric(pd.Series([row.get("personId")]), errors="coerce").iloc[0]
    if pd.isna(pid):
        return None
    pid = int(pid)
    return pid if 0 < pid < TEAM_ID_MIN else None


def resolved_team_id(row: pd.Series) -> int | None:
    tid = pd.to_numeric(pd.Series([row.get("teamId")]), errors="coerce").iloc[0]
    if pd.notna(tid) and int(tid) >= TEAM_ID_MIN:
        return int(tid)
    pid = pd.to_numeric(pd.Series([row.get("personId")]), errors="coerce").iloc[0]
    if pd.notna(pid) and int(pid) >= TEAM_ID_MIN:
        return int(pid)
    return None


def semantic_signature(row: pd.Series, source: str) -> tuple:
    gid = int(row.gameId)
    period = int(row.period)
    clock = str(row.clock)
    pid = player_person_id(row)
    if pid is not None:
        off = pd.to_numeric(pd.Series([row.get("reboundOffensiveTotal")]), errors="coerce").iloc[0]
        deff = pd.to_numeric(pd.Series([row.get("reboundDefensiveTotal")]), errors="coerce").iloc[0]
        if pd.isna(off) or pd.isna(deff):
            off2, def2 = parse_description_counters(row.get("description"))
            off = off2 if pd.isna(off) else int(off)
            deff = def2 if pd.isna(deff) else int(deff)
        return ("player", gid, period, clock, pid,
                None if pd.isna(off) else int(off), None if pd.isna(deff) else int(deff))
    return ("team", gid, period, clock, resolved_team_id(row))


def residual_rows(frame: pd.DataFrame, residual: Counter, source: str) -> list[dict]:
    need = residual.copy()
    out = []
    cols = [x for x in [
        "gameId", "actionNumber", "orderNumber", "clock", "period", "actionType", "subType",
        "description", "personId", "playerName", "teamId", "teamTricode", "reboundTotal",
        "reboundDefensiveTotal", "reboundOffensiveTotal", "shotResult", "possession"
    ] if x in frame]
    for _, row in frame.sort_values(["gameId", "period", "actionNumber"], kind="stable").iterrows():
        sig = semantic_signature(row, source)
        if need[sig] <= 0:
            continue
        rec = {k: (None if pd.isna(row[k]) else row[k]) for k in cols}
        rec["semantic_signature"] = list(sig)
        out.append(rec)
        need[sig] -= 1
    return out


def dead_team_placeholder_reason(row: pd.Series, source_frame: pd.DataFrame) -> str | None:
    """Return an evidence-backed reason only for known non-live team rebound forms.

    This is deliberately narrow. Any residual team rebound outside these exact source
    patterns remains a hard failure.
    """
    if semantic_signature(row, "cdn")[0] != "team":
        return None
    gid = int(row.gameId)
    period = int(row.period)
    action = int(row.actionNumber)
    clock = str(row.clock)
    game = source_frame[source_frame.gameId.eq(gid)].sort_values("actionNumber", kind="stable")

    # Modern CDN retains a small number of 0.1-second bookkeeping rebounds after the
    # period has already ended. These are the modern equivalent of historical buzzer/
    # heave placeholders and are not live rebound opportunities.
    if clock == "PT00M00.10S":
        prior = game[game.actionNumber.lt(action)]
        ended = prior[
            pd.to_numeric(prior.get("period"), errors="coerce").eq(period)
            & prior.action_norm.isin(["period", "game"])
            & prior.get("subType", pd.Series(index=prior.index, dtype=object)).astype("string").str.lower().isin(["end"])
        ]
        if not ended.empty:
            return "post_period_end_0.1s_placeholder"

    # A team rebound immediately after a missed non-final free throw is bookkeeping,
    # not a live rebound. Require same game/period/clock and the immediately preceding
    # action to state both MISS and a non-final x-of-y trip.
    prior = game[game.actionNumber.lt(action)].tail(1)
    if len(prior) == 1:
        p = prior.iloc[0]
        if int(p.period) == period and str(p.clock) == clock and str(p.action_norm) in {"freethrow", "free throw"}:
            desc = str(p.get("description", ""))
            subtype = str(p.get("subType", ""))
            m = re.search(r"(\d+)\s+of\s+(\d+)", f"{subtype} {desc}", re.I)
            if m and int(m.group(1)) < int(m.group(2)) and "miss" in desc.lower():
                return "non_live_intermediate_free_throw_placeholder"
    return None


def validate_player_counters(frame: pd.DataFrame, source: str) -> tuple[int, int, list[dict]]:
    rows = []
    for _, row in frame.iterrows():
        pid = player_person_id(row)
        if pid is None:
            continue
        off = pd.to_numeric(pd.Series([row.get("reboundOffensiveTotal")]), errors="coerce").iloc[0]
        deff = pd.to_numeric(pd.Series([row.get("reboundDefensiveTotal")]), errors="coerce").iloc[0]
        total = pd.to_numeric(pd.Series([row.get("reboundTotal")]), errors="coerce").iloc[0]
        if pd.isna(off) or pd.isna(deff):
            off2, def2 = parse_description_counters(row.get("description"))
            off = off2 if pd.isna(off) else off
            deff = def2 if pd.isna(deff) else deff
        if pd.isna(total) and pd.notna(off) and pd.notna(deff):
            total = int(off) + int(deff)
        rows.append((int(row.gameId), pid, int(row.actionNumber), int(total) if pd.notna(total) else None,
                     int(off) if pd.notna(off) else None, int(deff) if pd.notna(deff) else None))
    pdf = pd.DataFrame(rows, columns=["gameId", "personId", "actionNumber", "total", "off", "def"])
    if pdf.empty:
        return 0, 0, []
    pdf = pdf.sort_values(["gameId", "personId", "actionNumber"], kind="stable")
    complete = pdf[["total", "off", "def"]].notna().all(axis=1)
    checked = pdf[complete].copy()
    failures = []
    for (gid, pid), g in checked.groupby(["gameId", "personId"], sort=False):
        prev = (0, 0, 0)
        for _, r in g.iterrows():
            cur = (int(r.total), int(r.off), int(r["def"]))
            delta = tuple(cur[i] - prev[i] for i in range(3))
            if delta[0] != 1 or delta[1] + delta[2] != 1 or delta[1] not in (0, 1) or delta[2] not in (0, 1):
                failures.append({"source": source, "gameId": int(gid), "personId": int(pid),
                                 "actionNumber": int(r.actionNumber), "current": cur, "previous": prev, "delta": delta})
            prev = cur
    return len(pdf), len(checked), failures


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
        f["period"] = pd.to_numeric(f["period"], errors="raise").astype("int64")

    c = cdn[cdn.action_norm.eq("rebound")].copy()
    n = nba[nba.action_norm.eq("rebound")].copy()
    if c.empty or n.empty:
        raise SystemExit("2025 rebound semantic gate failed: no rebound actions")

    # Keep raw action-key diagnostics, but do not mistake feed renumbering/reordering for
    # a semantic disagreement.
    ckeys = set(zip(c.gameId, c.actionNumber))
    nkeys = set(zip(n.gameId, n.actionNumber))
    shared_action = ckeys & nkeys
    cdn_only_action = ckeys - nkeys
    nba_only_action = nkeys - ckeys

    c_sem = Counter(semantic_signature(r, "cdn") for _, r in c.iterrows())
    n_sem = Counter(semantic_signature(r, "nba") for _, r in n.iterrows())
    shared_sem = c_sem & n_sem
    c_res = c_sem - n_sem
    n_res = n_sem - c_sem

    c_res_rows = residual_rows(c, c_res, "cdn")
    n_res_rows = residual_rows(n, n_res, "nba")

    accepted_dead = []
    unresolved_cdn = []
    for rec in c_res_rows:
        row = c[(c.gameId.eq(int(rec["gameId"]))) & (c.actionNumber.eq(int(rec["actionNumber"])))].iloc[0]
        reason = dead_team_placeholder_reason(row, cdn)
        if reason:
            rec = dict(rec)
            rec["excluded_reason"] = reason
            accepted_dead.append(rec)
        else:
            unresolved_cdn.append(rec)

    # Do not automatically exclude V3-only residuals. V3 is the independent second feed;
    # any semantic rebound appearing only there must be reconciled or production stops.
    unresolved_nba = n_res_rows

    c_total, c_checked, c_fail = validate_player_counters(c, "cdn")
    n_total, n_checked, n_fail = validate_player_counters(n, "nba")

    payload = {
        "cdn_rebound_rows": int(len(c)),
        "nba_rebound_rows": int(len(n)),
        "raw_action_key_comparison": {
            "shared": int(len(shared_action)),
            "cdn_only": int(len(cdn_only_action)),
            "nba_only": int(len(nba_only_action)),
            "cdn_only_rows": records_for_keys(c, cdn_only_action),
            "nba_only_rows": records_for_keys(n, nba_only_action),
        },
        "semantic_identity_comparison": {
            "cdn_count": int(sum(c_sem.values())),
            "nba_count": int(sum(n_sem.values())),
            "shared_count": int(sum(shared_sem.values())),
            "cdn_residual_count": int(sum(c_res.values())),
            "nba_residual_count": int(sum(n_res.values())),
            "accepted_non_live_cdn_residuals": accepted_dead,
            "unresolved_cdn_residuals": unresolved_cdn,
            "unresolved_nba_residuals": unresolved_nba,
        },
        "player_counter_validation": {
            "cdn_player_rows": int(c_total),
            "cdn_complete_counter_rows": int(c_checked),
            "cdn_failures": c_fail,
            "nba_player_rows": int(n_total),
            "nba_complete_counter_rows": int(n_checked),
            "nba_failures": n_fail,
        },
        "semantics": {
            "comparison_key": "semantic rebound identity/multiplicity, not raw actionNumber",
            "player_rebounds": "must have valid cumulative Off/Def progression in source; cross-feed action renumbering is permitted only when semantic identity is equal",
            "team_rebounds": "one-sided rows are excluded only for explicit non-live free-throw or post-period-end placeholder evidence; all other residuals hard-fail",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))

    unresolved = len(unresolved_cdn) + len(unresolved_nba)
    counter_failures = len(c_fail) + len(n_fail)
    incomplete_counters = (c_total - c_checked) + (n_total - n_checked)
    if unresolved or counter_failures or incomplete_counters:
        raise SystemExit("2025 rebound semantic gate failed")
    print("TREB_2025_REBOUND_SEMANTICS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
