#!/usr/bin/env python3
"""Validate modern live/dead team-rebound semantics against 2024 legacy NBA events.

2024-25 is the bridge season because both the legacy NBA feed and the modern
NBA Stats v3 feed are available for the same games and action numbers.  The
historical engine's event-level ``_nba_real_rebound`` decision is the label.

The modern candidate below mirrors the five historical dead-ball mechanisms:
1. modern ``Normal Rebound`` placeholders (legacy non-zero rebound action);
2. same-clock turnover/violation placeholders;
3. non-final/flagrant missed free throws;
4. exact horn/end-period team rebounds;
5. near-horn team rebounds sharing the terminal timestamp.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core

PLAYER_MAX = core.PLAYER_MAX


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


def legacy_labels(nba: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for game_id, ng in nba.groupby("GAME_ID", sort=False):
        ng = ng.sort_values(["PERIOD", "EVENTNUM"], kind="stable").copy()
        ng["DESCRIPTION_NORM"] = core.nba_description(ng)
        ng["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(ng.PERIOD, ng.PCTIMESTRING)]
        for idx, row in ng[ng.EVENTMSGTYPE.eq(4)].iterrows():
            rows.append({
                "GAMEID": int(game_id),
                "NBA_EVENTNUM": int(row.EVENTNUM),
                "LEGACY_REAL": bool(core._nba_real_rebound(ng, idx)),
                "NBA_PLAYER1_ID": int(row.PLAYER1_ID) if pd.notna(row.PLAYER1_ID) else 0,
                "NBA_DESCRIPTION": str(row.DESCRIPTION_NORM),
            })
    out = pd.DataFrame(rows)
    if out.duplicated(["GAMEID", "NBA_EVENTNUM"]).any():
        raise SystemExit("duplicate legacy NBA rebound event keys")
    return out


def _period_terminal_after(game: pd.DataFrame, indices: list[int], pos: int, period: int) -> bool:
    """Return true when the next meaningful row is the period terminator/next period."""
    for j in range(pos + 1, len(indices)):
        cand = indices[j]
        cand_period = int(game.at[cand, "period"])
        if cand_period != period:
            return True
        action = norm(game.at[cand, "actionType"])
        if action in {"replay", "instant replay"}:
            continue
        if action in {"period", "game", "end period", "end game"}:
            return True
        return False
    return True


def _missed_nonfinal_or_special_ft(game: pd.DataFrame, prior: int | None) -> bool:
    if prior is None:
        return False
    action = norm(game.at[prior, "actionType"])
    desc = norm(game.at[prior, "description"])
    subtype = norm(game.at[prior, "subType"] if "subType" in game else "")
    descriptor = norm(game.at[prior, "descriptor"] if "descriptor" in game else "")
    shot_result = norm(game.at[prior, "shotResult"] if "shotResult" in game else "")
    if "free throw" not in action and "freethrow" not in action and "free throw" not in desc:
        return False
    if shot_result not in {"missed", "miss"} and "miss" not in desc:
        return False

    # NBA descriptions consistently expose x of y for ordinary trips. A missed
    # free throw is live only on the final attempt; technical/flagrant attempts
    # are administrative even when text numbering is absent.
    numbered = re.search(r"free throw\s+(\d+)\s+of\s+(\d+)", desc)
    if numbered and int(numbered.group(1)) < int(numbered.group(2)):
        return True
    special_text = " ".join((desc, subtype, descriptor))
    if "technical" in special_text or "flagrant" in special_text or "clear path" in special_text:
        return True
    return False


def modern_candidate_rule(game: pd.DataFrame) -> pd.Series:
    game = game.sort_values(["period", "orderNumber", "actionNumber"], kind="stable").copy()
    result = pd.Series(True, index=game.index, dtype=bool)
    actions = game.actionType.astype("string").fillna("").str.lower()
    rebound = actions.eq("rebound")
    person = pd.to_numeric(game.personId, errors="coerce").fillna(0)
    team = rebound & ~(person.gt(0) & person.lt(PLAYER_MAX))
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

        # 1. 2024 bridge proves modern Normal Rebound maps one-for-one to the
        # historical non-zero team rebound action, which is always dead-ball.
        if norm(game.at[idx, "subType"] if "subType" in game else "") == "normal rebound":
            result.at[idx] = False
            continue

        # Find nearest previous meaningful action.
        prior = None
        for j in range(pos - 1, -1, -1):
            cand = indices[j]
            if int(game.at[cand, "period"]) != period:
                break
            if norm(game.at[cand, "actionType"]) in {"replay", "instant replay"}:
                continue
            prior = cand
            break

        # 2. Historical turnover placeholder uses same-clock evidence. Search
        # both directions because modern feeds often emit the turnover after
        # the synthetic team rebound at exactly the same clock.
        for cand in indices:
            if cand == idx or int(game.at[cand, "period"]) != period:
                continue
            if abs(clocks[cand] - clock) > 0.011:
                continue
            action = norm(game.at[cand, "actionType"])
            desc = norm(game.at[cand, "description"])
            if "turnover" in action or "violation" in action or "turnover" in desc or "violation" in desc:
                dead = True
                break

        # 3. Non-final/flagrant free-throw misses do not create live rebounds.
        if not dead and _missed_nonfinal_or_special_ft(game, prior):
            dead = True

        terminal = _period_terminal_after(game, indices, pos, period)

        # 4. Exact horn/end-period team rebound.
        if not dead and clock <= 0.011 and terminal:
            dead = True

        # 5. Near-horn bookkeeping rebound: terminal timestamp within three
        # seconds of the horn and shares that clock with the preceding action.
        if not dead and clock <= 3.011 and terminal and prior is not None:
            if abs(clocks[prior] - clock) <= 0.011:
                dead = True

        result.at[idx] = not dead
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=False)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    nba = pd.read_csv(args.nba, low_memory=False)
    v3 = pd.read_csv(args.v3, low_memory=False)
    nba["GAME_ID"] = pd.to_numeric(nba.GAME_ID, errors="raise").astype("int64")
    if "orderNumber" not in v3.columns:
        v3["orderNumber"] = v3["actionNumber"]
    for col in ("gameId", "actionNumber", "orderNumber", "period", "personId"):
        v3[col] = pd.to_numeric(v3[col], errors="coerce")
    v3["gameId"] = v3.gameId.astype("int64")
    v3["actionNumber"] = v3.actionNumber.astype("int64")
    v3["orderNumber"] = v3.orderNumber.fillna(v3.actionNumber).astype("int64")
    v3["period"] = v3.period.astype("int64")

    labels = legacy_labels(nba)
    v3["MODERN_REAL_CANDIDATE"] = True
    for _, idx in v3.groupby("gameId", sort=False).groups.items():
        rule = modern_candidate_rule(v3.loc[idx])
        v3.loc[rule.index, "MODERN_REAL_CANDIDATE"] = rule

    reb = v3[v3.actionType.astype("string").fillna("").str.lower().eq("rebound")].copy()
    merged = reb.merge(labels, left_on=["gameId", "actionNumber"], right_on=["GAMEID", "NBA_EVENTNUM"], how="left", validate="one_to_one")
    person = pd.to_numeric(merged.personId, errors="coerce").fillna(0)
    team = merged[~(person.gt(0) & person.lt(PLAYER_MAX))].copy()
    labeled = team[team.LEGACY_REAL.notna()].copy()
    labeled["LEGACY_REAL"] = labeled.LEGACY_REAL.astype(bool)
    labeled["MODERN_REAL_CANDIDATE"] = labeled.MODERN_REAL_CANDIDATE.astype(bool)
    mism = labeled[labeled.LEGACY_REAL.ne(labeled.MODERN_REAL_CANDIDATE)].copy()

    def recs(df: pd.DataFrame, n: int = 200):
        cols = [c for c in ["gameId", "actionNumber", "orderNumber", "clock", "period", "teamId", "teamTricode", "personId", "description", "descriptor", "subType", "actionType", "LEGACY_REAL", "MODERN_REAL_CANDIDATE", "NBA_PLAYER1_ID", "NBA_DESCRIPTION"] if c in df]
        head = df[cols].head(n)
        return head.where(pd.notna(head), None).to_dict("records")

    payload = {
        "legacy_rebound_events": int(len(labels)),
        "modern_rebound_rows": int(len(reb)),
        "modern_team_or_nonplayer_rebound_rows": int(len(team)),
        "team_rows_with_legacy_event_labels": int(len(labeled)),
        "team_rows_without_legacy_event_labels": int(team.LEGACY_REAL.isna().sum()),
        "legacy_real_team_rows": int(labeled.LEGACY_REAL.sum()),
        "legacy_dead_team_rows": int((~labeled.LEGACY_REAL).sum()),
        "candidate_rule_mismatches": int(len(mism)),
        "candidate_rule_accuracy": float(1 - len(mism) / len(labeled)) if len(labeled) else None,
        "false_dead": int((mism.LEGACY_REAL & ~mism.MODERN_REAL_CANDIDATE).sum()) if len(mism) else 0,
        "false_live": int((~mism.LEGACY_REAL & mism.MODERN_REAL_CANDIDATE).sum()) if len(mism) else 0,
        "mismatch_samples": recs(mism),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "mismatch_samples"}, indent=2))
    print("BRIDGE_MISMATCHES", len(mism))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
