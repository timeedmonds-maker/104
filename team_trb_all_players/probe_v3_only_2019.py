#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import pandas as pd

GAMES = [21901316, 21901317, 21901318]


def norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def clean_records(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    if df.empty:
        return []
    x = df[[c for c in columns if c in df.columns]].copy()
    return x.where(pd.notna(x), None).to_dict("records")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    d = pd.read_csv(args.csv, low_memory=False)
    cols = list(d.columns)
    game_col = next((c for c in cols if c.lower() == "gameid"), None)
    if game_col is None:
        raise SystemExit("no gameId column")
    gid = pd.to_numeric(d[game_col], errors="coerce")

    likely_action_cols = [c for c in cols if any(k in c.lower() for k in ("action", "type", "sub", "descriptor", "description", "clock", "period", "team", "person", "rebound", "possession", "order"))]
    payload = {"source_rows": int(len(d)), "columns": cols, "likely_action_columns": likely_action_cols, "games": []}

    for game in GAMES:
        g = d.loc[gid.eq(game)].copy()
        row = {
            "game_id": game,
            "row_count": int(len(g)),
            "periods": sorted(pd.to_numeric(g.get("period", pd.Series(dtype=float)), errors="coerce").dropna().astype(int).unique().tolist()) if "period" in g else [],
            "nonnull_counts": {c: int(g[c].notna().sum()) for c in likely_action_cols},
        }
        for c in ["actionType", "subType", "descriptor", "periodType"]:
            if c in g:
                row[c + "_values"] = sorted({str(x) for x in g[c].dropna().unique()})[:200]

        text_cols = [c for c in ["description", "actionType", "subType", "descriptor"] if c in g]
        mask = pd.Series(False, index=g.index)
        for c in text_cols:
            s = g[c].map(norm)
            mask |= s.str.contains(r"sub|rebound|enters|replace|starter", regex=True)
        detail_cols = [c for c in [
            "gameId", "period", "clock", "actionNumber", "actionId", "orderNumber",
            "actionType", "subType", "descriptor", "description", "teamId", "teamTricode",
            "personId", "playerName", "possession", "reboundTotal", "reboundDefensiveTotal",
            "reboundOffensiveTotal", "shotResult", "isFieldGoal", "scoreHome", "scoreAway",
        ] if c in g]
        row["sub_or_rebound_rows"] = clean_records(g.loc[mask].head(300), detail_cols)
        row["first_rows_by_period"] = {}
        if "period" in g:
            for p, pg in g.groupby("period", sort=True):
                row["first_rows_by_period"][str(int(p))] = clean_records(pg.head(15), detail_cols)
        payload["games"].append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"columns": cols, "games": [(x["game_id"], x["row_count"]) for x in payload["games"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
