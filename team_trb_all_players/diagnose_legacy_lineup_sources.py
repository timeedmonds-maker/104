#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

CASES = [
    (20300300, 1, 1610612764, [363, 1716, 2041, 2240, 2553, 2581]),
    (20300442, 4, 1610612741, [383, 916, 1944, 2037, 2201, 2669]),
    (20300594, 2, 1610612762, [731, 1952, 2052, 2221, 2260, 2590]),
    (20300785, 3, 1610612753, [436, 1503, 1731, 2052, 2585, 2586]),
    (20300787, 4, 1610612748, [902, 961, 2254, 2406, 2446, 2617]),
    (20300915, 3, 1610612742, [281, 714, 952, 959, 1504, 1717]),
    (20301110, 2, 1610612759, [1495, 1725, 1938, 2045, 2078, 2484]),
]


def scalar(v):
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def records(df: pd.DataFrame, cols: list[str], limit: int = 200):
    if df.empty:
        return []
    cols = [c for c in cols if c in df.columns]
    out = []
    for row in df[cols].head(limit).to_dict("records"):
        out.append({k: scalar(v) for k, v in row.items()})
    return out


def pick_columns(df: pd.DataFrame, preferred: list[str]) -> list[str]:
    exact = [c for c in preferred if c in df.columns]
    hints = ("player", "person", "team", "opponent", "lineup", "offensive", "defensive", "action", "description", "sub", "clock", "period", "order", "game")
    hinted = [c for c in df.columns if any(h in c.lower() for h in hints)]
    seen, out = set(), []
    for c in exact + hinted:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    nba = pd.read_csv(a.nba, low_memory=False)
    pbp = pd.read_csv(a.pbp, low_memory=False)
    v3 = pd.read_csv(a.v3, low_memory=False)

    payload = {
        "nba_columns": list(nba.columns),
        "pbp_columns": list(pbp.columns),
        "v3_columns": list(v3.columns),
        "pbp_possible_lineup_columns": [c for c in pbp.columns if any(x in c.lower() for x in ("player", "lineup", "offensive", "defensive"))],
        "v3_possible_lineup_columns": [c for c in v3.columns if any(x in c.lower() for x in ("player", "lineup", "offensive", "defensive", "person"))],
        "cases": [],
    }

    nba_gid = pd.to_numeric(nba.get("GAME_ID"), errors="coerce")
    pbp_gid = pd.to_numeric(pbp.get("GAMEID"), errors="coerce")
    v3_gid = pd.to_numeric(v3.get("gameId"), errors="coerce")
    v3_period = pd.to_numeric(v3.get("period"), errors="coerce")

    v3_view_cols = pick_columns(v3, ["gameId", "actionNumber", "orderNumber", "period", "clock", "actionType", "subType", "descriptor", "description", "personId", "playerName", "playerNameI", "teamId", "teamTricode"])
    pbp_view_cols = pick_columns(pbp, ["GAMEID", "PERIOD", "STARTTIME", "ENDTIME", "OPPONENT", "DESCRIPTION", "OFFENSIVEREBOUNDS"])
    nba_view_cols = pick_columns(nba, ["GAME_ID", "EVENTNUM", "EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PERIOD", "PCTIMESTRING", "PERSON1TYPE", "PERSON2TYPE", "PERSON3TYPE", "PLAYER1_ID", "PLAYER1_NAME", "PLAYER1_TEAM_ID", "PLAYER2_ID", "PLAYER2_NAME", "PLAYER2_TEAM_ID", "PLAYER3_ID", "PLAYER3_NAME", "PLAYER3_TEAM_ID", "HOMEDESCRIPTION", "VISITORDESCRIPTION", "NEUTRALDESCRIPTION"])

    for game_id, period, team_id, candidates in CASES:
        ng = nba[nba_gid.eq(game_id)].copy()
        pg = pbp[pbp_gid.eq(game_id)].copy()
        vg = v3[v3_gid.eq(game_id)].copy()
        npd = ng[pd.to_numeric(ng.get("PERIOD"), errors="coerce").eq(period)].copy()
        ppd = pg[pd.to_numeric(pg.get("PERIOD"), errors="coerce").eq(period)].copy()
        vpd = vg[v3_period.loc[vg.index].eq(period)].copy()

        person = pd.to_numeric(vpd.get("personId"), errors="coerce") if "personId" in vpd else pd.Series(index=vpd.index, dtype=float)
        candidate_rows = vpd[person.isin(candidates)].copy()
        action_norm = vpd.get("actionType", pd.Series(index=vpd.index, dtype="string")).astype("string").fillna("").str.lower()
        substitution_rows = vpd[action_norm.str.contains("sub", na=False)].copy()

        evt = pd.to_numeric(npd.get("EVENTMSGTYPE"), errors="coerce")
        p1 = pd.to_numeric(npd.get("PLAYER1_ID"), errors="coerce")
        p2 = pd.to_numeric(npd.get("PLAYER2_ID"), errors="coerce")
        p3 = pd.to_numeric(npd.get("PLAYER3_ID"), errors="coerce")
        legacy_sub = npd[evt.eq(8) & (p1.isin(candidates) | p2.isin(candidates))].copy()
        legacy_candidate = npd[p1.isin(candidates) | p2.isin(candidates) | p3.isin(candidates)].copy()

        first_by_candidate = {}
        legacy_by_candidate = {}
        for pid in candidates:
            rows = candidate_rows[pd.to_numeric(candidate_rows.get("personId"), errors="coerce").eq(pid)] if not candidate_rows.empty and "personId" in candidate_rows else candidate_rows.iloc[0:0]
            first_by_candidate[str(pid)] = records(rows, v3_view_cols, 8)
            lp1 = pd.to_numeric(legacy_candidate.get("PLAYER1_ID"), errors="coerce")
            lp2 = pd.to_numeric(legacy_candidate.get("PLAYER2_ID"), errors="coerce")
            lp3 = pd.to_numeric(legacy_candidate.get("PLAYER3_ID"), errors="coerce")
            lrows = legacy_candidate[lp1.eq(pid) | lp2.eq(pid) | lp3.eq(pid)]
            legacy_by_candidate[str(pid)] = records(lrows, nba_view_cols, 30)

        payload["cases"].append({
            "game_id": game_id,
            "period": period,
            "team_id": team_id,
            "legacy_six_candidates": candidates,
            "source_row_counts": {"legacy_nba_game": len(ng), "pbpstats_game": len(pg), "v3_game": len(vg), "v3_period": len(vpd)},
            "legacy_candidate_substitution_rows": records(legacy_sub, nba_view_cols, 80),
            "legacy_rows_by_candidate": legacy_by_candidate,
            "v3_substitution_rows_period": records(substitution_rows, v3_view_cols, 120),
            "v3_candidate_rows": records(candidate_rows, v3_view_cols, 160),
            "v3_first_rows_by_candidate": first_by_candidate,
            "pbpstats_period_sample": records(ppd, pbp_view_cols, 25),
        })

    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "pbp_possible_lineup_columns": payload["pbp_possible_lineup_columns"],
        "v3_possible_lineup_columns": payload["v3_possible_lineup_columns"],
        "case_counts": [{"game_id": x["game_id"], **x["source_row_counts"]} for x in payload["cases"]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
