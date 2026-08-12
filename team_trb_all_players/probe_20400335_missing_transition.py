#!/usr/bin/env python3
"""Extract source evidence around the 2004-05 NOH missing lineup transition.

Game 20400335 cannot be repaired by choosing different period-2 starters:
J.R. Smith (2747) appears as an on-court participant after the opening lineup
without a recorded legacy substitution.  This probe records both legacy and V3
chronology around his first action, all NOH period-2 substitutions, and the
candidate player's event clocks so any synthetic transition can be justified
by source evidence rather than guessed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

GAME_ID = 20400335
TEAM_ID = 1610612740
JR_SMITH = 2747
CANDIDATE_OUT = 2454
PERIOD = 2


def norm(x: object) -> str:
    if pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def records(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    if df.empty:
        return []
    x = df[[c for c in columns if c in df.columns]].copy()
    return x.where(pd.notna(x), None).to_dict("records")


def legacy_player_in_row(row: pd.Series, pid: int) -> bool:
    return any(int(pd.to_numeric(row.get(f"PLAYER{i}_ID", 0), errors="coerce") or 0) == pid for i in (1, 2, 3))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    legacy = pd.read_csv(a.legacy, low_memory=False)
    v3 = pd.read_csv(a.v3, low_memory=False)
    for c in ("GAME_ID", "PERIOD", "EVENTNUM", "EVENTMSGTYPE", "PLAYER1_ID", "PLAYER2_ID", "PLAYER3_ID", "PLAYER1_TEAM_ID", "PLAYER2_TEAM_ID", "PLAYER3_TEAM_ID"):
        if c in legacy:
            legacy[c] = pd.to_numeric(legacy[c], errors="coerce")
    for c in ("gameId", "period", "actionNumber", "actionId", "personId", "teamId"):
        if c in v3:
            v3[c] = pd.to_numeric(v3[c], errors="coerce")

    lg = legacy[(legacy.GAME_ID == GAME_ID) & (legacy.PERIOD == PERIOD)].copy()
    vg = v3[(v3.gameId == GAME_ID) & (v3.period == PERIOD)].copy().sort_values(["actionId", "actionNumber"], kind="stable")
    if lg.empty or vg.empty:
        raise SystemExit(f"missing game source legacy={len(lg)} v3={len(vg)}")

    v3_jr = vg[vg.personId.eq(JR_SMITH)]
    if v3_jr.empty:
        raise SystemExit("J.R. Smith absent from V3 period 2")
    first_jr = v3_jr.iloc[0]
    first_action_id = int(first_jr.actionId)
    first_action_number = int(first_jr.actionNumber)

    v3_cols = [
        "gameId", "period", "clock", "actionNumber", "actionId", "actionType", "subType",
        "description", "teamId", "teamTricode", "personId", "playerName", "scoreHome", "scoreAway",
    ]
    legacy_cols = [
        "GAME_ID", "PERIOD", "PCTIMESTRING", "EVENTNUM", "EVENTMSGTYPE", "EVENTMSGACTIONTYPE",
        "HOMEDESCRIPTION", "NEUTRALDESCRIPTION", "VISITORDESCRIPTION",
        "PLAYER1_ID", "PLAYER1_NAME", "PLAYER1_TEAM_ID",
        "PLAYER2_ID", "PLAYER2_NAME", "PLAYER2_TEAM_ID",
        "PLAYER3_ID", "PLAYER3_NAME", "PLAYER3_TEAM_ID",
    ]

    # V3 substitutions for the affected team; substitution rows expose the
    # outgoing player in personId and both names in description.
    sub_mask = vg.actionType.astype("string").str.lower().eq("substitution") & vg.teamId.eq(TEAM_ID)
    team_subs = vg[sub_mask]

    # All V3 events involving either J.R. Smith or the likely displaced player.
    focus = vg[vg.personId.isin([JR_SMITH, CANDIDATE_OUT])]

    # Tight chronological window around J.R.'s first recorded action.
    pos = vg.index.get_loc(first_jr.name)
    if isinstance(pos, slice):
        pos = pos.start
    window = vg.iloc[max(0, int(pos) - 25): min(len(vg), int(pos) + 26)]

    # Legacy rows explicitly naming either player and all period-2 substitutions.
    legacy_focus = lg[lg.apply(lambda r: legacy_player_in_row(r, JR_SMITH) or legacy_player_in_row(r, CANDIDATE_OUT), axis=1)]
    legacy_subs = lg[lg.EVENTMSGTYPE.eq(8)]

    # Candidate transition clocks are stoppage/action clocks between the last
    # prior event by 2454 and first J.R. action.  This is only an evidence set;
    # no clock is promoted by this probe.
    before_jr = vg[vg.actionId.lt(first_action_id)]
    prior_out = before_jr[before_jr.personId.eq(CANDIDATE_OUT)]
    last_out_action_id = int(prior_out.actionId.max()) if not prior_out.empty else None
    interval = vg[(vg.actionId > (last_out_action_id if last_out_action_id is not None else -1)) & (vg.actionId <= first_action_id)]
    candidate_clocks = []
    for _, r in interval.iterrows():
        candidate_clocks.append({
            "action_id": int(r.actionId),
            "action_number": int(r.actionNumber),
            "clock": str(r.clock),
            "action_type": norm(r.get("actionType")),
            "description": norm(r.get("description")),
            "team_id": int(r.teamId) if pd.notna(r.teamId) else None,
            "person_id": int(r.personId) if pd.notna(r.personId) else None,
        })

    payload = {
        "game_id": GAME_ID,
        "period": PERIOD,
        "team_id": TEAM_ID,
        "player_in": JR_SMITH,
        "candidate_player_out": CANDIDATE_OUT,
        "legacy_rows_period": int(len(lg)),
        "v3_rows_period": int(len(vg)),
        "first_jr_v3_action": records(v3_jr.head(1), v3_cols)[0],
        "last_candidate_out_action_id_before_jr": last_out_action_id,
        "v3_team_substitutions": records(team_subs, v3_cols),
        "v3_focus_player_events": records(focus, v3_cols),
        "v3_window_around_first_jr": records(window, v3_cols),
        "legacy_substitutions": records(legacy_subs, legacy_cols),
        "legacy_focus_player_rows": records(legacy_focus, legacy_cols),
        "candidate_transition_interval": candidate_clocks,
        "policy": "Evidence extraction only. Do not add a synthetic transition unless a unique source/minute-consistent transition is established.",
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "first_jr": payload["first_jr_v3_action"],
        "last_candidate_out_action_id_before_jr": last_out_action_id,
        "team_substitutions": len(payload["v3_team_substitutions"]),
        "candidate_transition_interval_rows": len(candidate_clocks),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
