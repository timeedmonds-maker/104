#!/usr/bin/env python3
"""Map modern 2024 rebound fields directly to legacy NBA rebound action types.

The historical engine treats non-player rebound EVENTMSGACTIONTYPE as a primary
live/dead signal.  2024 provides one-to-one legacy and modern action numbers,
so this diagnostic asks whether modern ``actionId`` / ``subType`` / descriptor
fields encode that signal directly enough to transfer to 2025-26.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core

PLAYER_MAX = core.PLAYER_MAX


def clean(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    nba = pd.read_csv(args.nba, low_memory=False)
    v3 = pd.read_csv(args.v3, low_memory=False)
    nba["GAME_ID"] = pd.to_numeric(nba.GAME_ID, errors="raise").astype("int64")
    nba["EVENTNUM"] = pd.to_numeric(nba.EVENTNUM, errors="raise").astype("int64")
    v3["gameId"] = pd.to_numeric(v3.gameId, errors="raise").astype("int64")
    v3["actionNumber"] = pd.to_numeric(v3.actionNumber, errors="raise").astype("int64")

    legacy_rows = []
    for game_id, ng0 in nba.groupby("GAME_ID", sort=False):
        ng = ng0.sort_values(["PERIOD", "EVENTNUM"], kind="stable").copy().reset_index(drop=True)
        ng["DESCRIPTION_NORM"] = core.nba_description(ng)
        ng["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(ng.PERIOD, ng.PCTIMESTRING)]
        for idx, row in ng[ng.EVENTMSGTYPE.eq(4)].iterrows():
            pid = int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0
            legacy_rows.append({
                "gameId": int(game_id),
                "actionNumber": int(row.EVENTNUM),
                "legacy_action_type": int(row.EVENTMSGACTIONTYPE),
                "legacy_real": bool(core._nba_real_rebound(ng, idx)),
                "legacy_player1_id": pid,
                "legacy_team_or_nonplayer": not (0 < pid < PLAYER_MAX),
                "legacy_description": str(row.DESCRIPTION_NORM),
            })
    legacy = pd.DataFrame(legacy_rows)

    modern = v3[v3.actionType.astype("string").fillna("").str.lower().eq("rebound")].copy()
    keep = [c for c in [
        "gameId", "actionNumber", "actionId", "actionType", "subType", "descriptor",
        "qualifiers", "description", "personId", "teamId", "teamTricode", "shotResult",
    ] if c in modern.columns]
    modern = modern[keep]
    merged = legacy.merge(modern, on=["gameId", "actionNumber"], how="outer", indicator=True, validate="one_to_one")
    if not merged._merge.eq("both").all():
        raise SystemExit(
            f"legacy/modern rebound key mismatch: {merged._merge.value_counts().to_dict()}"
        )
    team = merged[merged.legacy_team_or_nonplayer.astype(bool)].copy()

    dimension_cols = [c for c in ["actionId", "subType", "descriptor"] if c in team.columns]
    dimensions = {}
    for col in dimension_cols:
        grouped = (
            team.assign(_value=team[col].astype("string").fillna("<NA>"))
            .groupby(["legacy_action_type", "legacy_real", "_value"], dropna=False)
            .size()
            .reset_index(name="count")
            .sort_values(["legacy_action_type", "legacy_real", "count"], ascending=[True, True, False])
        )
        dimensions[col] = [
            {k: clean(v) for k, v in row.items()}
            for row in grouped.to_dict("records")
        ]

    action_summary = (
        team.groupby("legacy_action_type", dropna=False)
        .agg(rows=("actionNumber", "size"), real=("legacy_real", "sum"))
        .reset_index()
    )
    action_summary["dead"] = action_summary.rows - action_summary.real
    action_summary["real_rate"] = action_summary.real / action_summary.rows

    # Test the historical primary condition directly: for team/non-player rows,
    # action type zero is eligible to be real while non-zero is dead.
    team["legacy_action_zero_rule"] = team.legacy_action_type.eq(0)
    zero_rule_mismatch = team[team.legacy_action_zero_rule.ne(team.legacy_real.astype(bool))]

    combination_cols = [c for c in ["legacy_action_type", "legacy_real", "actionId", "subType", "descriptor"] if c in team.columns]
    combinations = (
        team.assign(
            subType=team.get("subType", pd.Series(index=team.index, dtype="string")).astype("string").fillna("<NA>"),
            descriptor=team.get("descriptor", pd.Series(index=team.index, dtype="string")).astype("string").fillna("<NA>"),
        )
        .groupby(combination_cols, dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )

    sample_cols = [c for c in [
        "gameId", "actionNumber", "legacy_action_type", "legacy_real", "legacy_description",
        "actionId", "subType", "descriptor", "qualifiers", "description", "personId", "teamId",
    ] if c in team.columns]
    samples = {}
    for action_type, grp in team.groupby("legacy_action_type", sort=True):
        head = grp[sample_cols].head(30)
        samples[str(int(action_type))] = [
            {k: clean(v) for k, v in row.items()} for row in head.to_dict("records")
        ]

    payload = {
        "legacy_rebound_rows": int(len(legacy)),
        "modern_rebound_rows": int(len(modern)),
        "team_nonplayer_rows": int(len(team)),
        "legacy_action_type_summary": [
            {k: clean(v) for k, v in row.items()} for row in action_summary.to_dict("records")
        ],
        "legacy_action_zero_rule_mismatches": int(len(zero_rule_mismatch)),
        "legacy_action_zero_rule_accuracy": float(1 - len(zero_rule_mismatch) / len(team)),
        "dimension_mappings": dimensions,
        "top_combinations": [
            {k: clean(v) for k, v in row.items()} for row in combinations.head(200).to_dict("records")
        ],
        "samples_by_legacy_action_type": samples,
        "zero_rule_mismatch_samples": [
            {k: clean(v) for k, v in row.items()}
            for row in zero_rule_mismatch[sample_cols].head(100).to_dict("records")
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "team_nonplayer_rows": payload["team_nonplayer_rows"],
        "legacy_action_type_summary": payload["legacy_action_type_summary"],
        "legacy_action_zero_rule_mismatches": payload["legacy_action_zero_rule_mismatches"],
        "legacy_action_zero_rule_accuracy": payload["legacy_action_zero_rule_accuracy"],
        "top_combinations": payload["top_combinations"][:30],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
