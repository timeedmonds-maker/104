#!/usr/bin/env python3
"""Validate a modern-feed live/dead team-rebound rule against 2024 PBP Stats.

2024-25 is the bridge season: legacy NBA Stats + PBP Stats are available and the
same games/actions also exist in NBA Stats v3.  The legacy engine remains the
label source.  A modern rule is acceptable for 2025 only if it reproduces those
labels on the bridge season with an explicitly audited exception set.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core
import production_treb_engine as prod


def norm(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def iso_clock_seconds(value: object) -> float:
    text = str(value)
    m = re.fullmatch(r"PT(?:(\d+)M)?([0-9.]+)S", text)
    if not m:
        raise ValueError(f"unsupported modern clock {text!r}")
    return 60 * int(m.group(1) or 0) + float(m.group(2))


def legacy_labels(nba: pd.DataFrame, pbp: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    audits = []
    for game_id, pg in pbp.groupby("GAMEID", sort=False):
        ng = nba[nba.GAME_ID.eq(int(game_id))].copy()
        if ng.empty:
            continue
        ng = ng.sort_values(["PERIOD", "EVENTNUM"], kind="stable").copy()
        ng["DESCRIPTION_NORM"] = core.nba_description(ng)
        ng["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(ng.PERIOD, ng.PCTIMESTRING)]
        # production_treb_engine.join_pbp_rebounds carries LINEUP through to its
        # output.  The bridge is labeling rebound semantics only, so no lineup
        # value is required; provide a neutral placeholder rather than running
        # lineup reconstruction and introducing an irrelevant dependency.
        ng["LINEUP"] = None

        class Shell:
            events = ng

        joined, audit = prod.join_pbp_rebounds(Shell(), pg)
        labeled = core.classify_rebounds(joined)
        if len(labeled):
            rows.append(
                labeled[["NBA_EVENTNUM", "IS_REAL_REBOUND", "IS_OREB", "DESCRIPTION", "NBA_PLAYER1_ID", "PERIOD", "STARTTIME", "ENDTIME"]]
                .assign(GAMEID=int(game_id))
            )
        audits.append(audit)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    summary = {
        k: int(sum(a.get(k, 0) for a in audits))
        for k in ["rebound_bearing_rows", "matched_rebound_bearing_rows", "unmatched_rebound_bearing_rows", "ambiguous_matches", "manual_join_repairs"]
    }
    return out, summary


def modern_candidate_rule(game: pd.DataFrame) -> pd.Series:
    game = game.sort_values(["period", "orderNumber", "actionNumber"], kind="stable").copy()
    result = pd.Series(True, index=game.index, dtype=bool)
    actions = game.actionType.astype("string").fillna("").str.lower()
    rebound = actions.eq("rebound")
    person = pd.to_numeric(game.personId, errors="coerce").fillna(0)
    team = rebound & ~person.gt(0)
    if not team.any():
        return result

    indices = list(game.index)
    position = {idx: i for i, idx in enumerate(indices)}
    clocks = {idx: iso_clock_seconds(game.at[idx, "clock"]) for idx in indices}
    for idx in game.index[team]:
        pos = position[idx]
        period = int(game.at[idx, "period"])
        clock = clocks[idx]
        dead = False

        prior = None
        j = pos - 1
        while j >= 0:
            cand = indices[j]
            if int(game.at[cand, "period"]) != period:
                break
            at = norm(game.at[cand, "actionType"])
            if at not in {"replay", "instant replay"}:
                prior = cand
                break
            j -= 1
        same_clock = []
        j = pos - 1
        while j >= 0:
            cand = indices[j]
            if int(game.at[cand, "period"]) != period or abs(clocks[cand] - clock) > 0.011:
                break
            same_clock.append(cand)
            j -= 1

        for cand in same_clock:
            at = norm(game.at[cand, "actionType"])
            desc = norm(game.at[cand, "description"])
            if "turnover" in at or "violation" in at or "turnover" in desc or "violation" in desc:
                dead = True
                break

        if not dead and prior is not None:
            at = norm(game.at[prior, "actionType"])
            desc = norm(game.at[prior, "description"])
            subtype = norm(game.at[prior, "subType"] if "subType" in game else "")
            if "freethrow" in at or "free throw" in desc:
                miss = ("miss" in desc) or norm(game.at[prior, "shotResult"] if "shotResult" in game else "") == "missed"
                if miss:
                    nonfinal = bool(re.search(r"free throw (?:1 of [23]|2 of 3)", desc))
                    special = "technical" in desc or "flagrant" in desc or "technical" in subtype or "flagrant" in subtype
                    if nonfinal or special:
                        dead = True

        if not dead and clock <= 0.11:
            next_live = None
            j = pos + 1
            while j < len(indices):
                cand = indices[j]
                if int(game.at[cand, "period"]) != period:
                    break
                at = norm(game.at[cand, "actionType"])
                if at not in {"replay", "instant replay"}:
                    next_live = cand
                    break
                j += 1
            if next_live is None or norm(game.at[next_live, "actionType"]) in {"period", "game", "end period", "end game"}:
                dead = True

        result.at[idx] = not dead
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    nba = pd.read_csv(args.nba, low_memory=False)
    pbp = pd.read_csv(args.pbp, low_memory=False)
    v3 = pd.read_csv(args.v3, low_memory=False)
    nba["GAME_ID"] = pd.to_numeric(nba.GAME_ID, errors="raise").astype("int64")
    pbp["GAMEID"] = pd.to_numeric(pbp.GAMEID, errors="raise").astype("int64")
    if "orderNumber" not in v3.columns:
        v3["orderNumber"] = v3["actionNumber"]
    for col in ("gameId", "actionNumber", "orderNumber", "period", "personId"):
        v3[col] = pd.to_numeric(v3[col], errors="coerce")
    v3["gameId"] = v3.gameId.astype("int64")
    v3["actionNumber"] = v3.actionNumber.astype("int64")
    v3["orderNumber"] = v3.orderNumber.fillna(v3.actionNumber).astype("int64")
    v3["period"] = v3.period.astype("int64")

    labels, audit = legacy_labels(nba, pbp)
    if labels.empty:
        raise SystemExit("no bridge labels")
    conflicts = labels.groupby(["GAMEID", "NBA_EVENTNUM"]).IS_REAL_REBOUND.nunique()
    bad = conflicts[conflicts.gt(1)]
    if len(bad):
        raise SystemExit(f"conflicting legacy labels for {len(bad)} action keys")
    label = labels.groupby(["GAMEID", "NBA_EVENTNUM"], as_index=False).agg(
        LEGACY_REAL=("IS_REAL_REBOUND", "first"),
        LEGACY_OREB=("IS_OREB", "first"),
        PBP_DESCRIPTION=("DESCRIPTION", "first"),
        NBA_PLAYER1_ID=("NBA_PLAYER1_ID", "first"),
    )

    v3["MODERN_REAL_CANDIDATE"] = True
    for _, idx in v3.groupby("gameId", sort=False).groups.items():
        rule = modern_candidate_rule(v3.loc[idx])
        v3.loc[rule.index, "MODERN_REAL_CANDIDATE"] = rule

    reb = v3[v3.actionType.astype("string").fillna("").str.lower().eq("rebound")].copy()
    merged = reb.merge(label, left_on=["gameId", "actionNumber"], right_on=["GAMEID", "NBA_EVENTNUM"], how="left", validate="one_to_one")
    team = merged[~pd.to_numeric(merged.personId, errors="coerce").fillna(0).gt(0)].copy()
    labeled_team = team[team.LEGACY_REAL.notna()].copy()
    labeled_team["LEGACY_REAL"] = labeled_team.LEGACY_REAL.astype(bool)
    labeled_team["MODERN_REAL_CANDIDATE"] = labeled_team.MODERN_REAL_CANDIDATE.astype(bool)
    mism = labeled_team[labeled_team.LEGACY_REAL.ne(labeled_team.MODERN_REAL_CANDIDATE)].copy()

    def recs(df, n=120):
        cols = [c for c in ["gameId", "actionNumber", "orderNumber", "clock", "period", "teamId", "teamTricode", "personId", "description", "descriptor", "subType", "actionType", "LEGACY_REAL", "MODERN_REAL_CANDIDATE", "LEGACY_OREB", "PBP_DESCRIPTION"] if c in df]
        head = df[cols].head(n)
        return head.where(pd.notna(head), None).to_dict("records")

    payload = {
        "legacy_join_audit": audit,
        "modern_rebound_rows": int(len(reb)),
        "modern_team_or_nonplayer_rebound_rows": int(len(team)),
        "team_rows_with_legacy_labels": int(len(labeled_team)),
        "team_rows_without_legacy_labels": int(team.LEGACY_REAL.isna().sum()),
        "legacy_real_team_rows": int(labeled_team.LEGACY_REAL.sum()),
        "legacy_dead_team_rows": int((~labeled_team.LEGACY_REAL).sum()),
        "candidate_rule_mismatches": int(len(mism)),
        "candidate_rule_accuracy": float(1 - len(mism) / len(labeled_team)) if len(labeled_team) else None,
        "mismatch_samples": recs(mism),
        "dead_samples": recs(labeled_team[~labeled_team.LEGACY_REAL]),
        "unlabeled_samples": recs(team[team.LEGACY_REAL.isna()]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if not k.endswith("samples")}, indent=2))
    print("BRIDGE_MISMATCHES", len(mism))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
