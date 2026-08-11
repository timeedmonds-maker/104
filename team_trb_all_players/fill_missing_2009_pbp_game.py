#!/usr/bin/env python3
"""Fill the one known PBP Stats source gap in 2009-10 from NBA Stats v3.

Game 20900212 is present in the legacy NBA Stats feed but absent from the
pbpstats_2009 archive.  This tool does not infer or fabricate rebounds.  It
uses NBA Stats v3 rebound actions for that game, requires a one-to-one match to
legacy NBA EVENTNUM/actionNumber, and writes PBP-shaped rows whose offensive /
defensive classification comes directly from v3 ``subType``.  The legacy NBA
row remains authoritative for lineup and live/dead rebound filtering.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

GAME_ID = 20900212


def nba_description(frame: pd.DataFrame) -> pd.Series:
    cols = [frame[c].fillna("").astype(str) for c in ("HOMEDESCRIPTION", "NEUTRALDESCRIPTION", "VISITORDESCRIPTION")]
    return (cols[0] + " " + cols[1] + " " + cols[2]).str.replace(r"\s+", " ", regex=True).str.strip()


def clock_seconds(value: object) -> int:
    minute, second = str(value).split(":")[:2]
    return int(minute) * 60 + int(float(second))


def format_clock(seconds: int, tag: int) -> str:
    seconds = max(0, int(seconds))
    minute, second = divmod(seconds, 60)
    # The harmless decimal tag keeps same-clock synthetic rows in distinct
    # PBP possession keys; the reconstruction clock parser intentionally uses
    # the integer second, so lineup timing is unchanged.
    return f"{minute:02d}:{second:02d}.{tag % 90 + 10:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", type=Path, required=True)
    ap.add_argument("--pbp", type=Path, required=True)
    ap.add_argument("--v3", type=Path, required=True)
    ap.add_argument("--audit", type=Path, required=True)
    args = ap.parse_args()

    nba = pd.read_csv(args.nba, low_memory=False)
    pbp = pd.read_csv(args.pbp, low_memory=False)
    v3 = pd.read_csv(args.v3, low_memory=False)

    if "GAMEID" in pbp:
        existing = pd.to_numeric(pbp.GAMEID, errors="coerce").eq(GAME_ID)
        if existing.any():
            payload = {"game_id": GAME_ID, "status": "already_present", "existing_rows": int(existing.sum())}
            args.audit.parent.mkdir(parents=True, exist_ok=True)
            args.audit.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0

    ng = nba[pd.to_numeric(nba.GAME_ID, errors="coerce").eq(GAME_ID)].copy()
    vg = v3[pd.to_numeric(v3.gameId, errors="coerce").eq(GAME_ID)].copy()
    if ng.empty or vg.empty:
        raise SystemExit(f"fallback source missing: legacy_nba={len(ng)} v3={len(vg)}")

    vg["action_norm"] = vg.actionType.astype("string").fillna("").str.strip().str.lower()
    rebounds = vg[vg.action_norm.eq("rebound")].copy()
    if rebounds.empty:
        raise SystemExit("v3 fallback game has no rebound actions")

    ng["EVENTNUM_INT"] = pd.to_numeric(ng.EVENTNUM, errors="raise").astype(int)
    ng["DESCRIPTION_SYNTH"] = nba_description(ng)
    by_event = {int(ev): grp for ev, grp in ng.groupby("EVENTNUM_INT", sort=False)}

    team_pairs = (
        vg[["teamId", "teamTricode"]]
        .dropna()
        .assign(teamId=lambda x: pd.to_numeric(x.teamId, errors="coerce"))
        .dropna()
        .drop_duplicates()
    )
    team_map = {int(r.teamId): str(r.teamTricode) for r in team_pairs.itertuples(index=False)}
    if len(team_map) != 2:
        raise SystemExit(f"expected two teams in v3 fallback game, got {team_map}")

    synthetic = []
    mapping = []
    for r in rebounds.itertuples(index=False):
        action = int(r.actionNumber)
        hit = by_event.get(action)
        if hit is None or len(hit) != 1:
            raise SystemExit(f"v3 rebound action {action} does not map one-to-one to legacy EVENTNUM")
        old = hit.iloc[0]
        if int(old.EVENTMSGTYPE) != 4:
            raise SystemExit(f"mapped legacy event {action} is not a rebound: type={old.EVENTMSGTYPE}")
        description = str(old.DESCRIPTION_SYNTH)
        if "rebound" not in description.lower():
            raise SystemExit(f"mapped legacy rebound {action} has no rebound description: {description!r}")

        subtype = str(r.subType).strip().lower()
        if subtype not in {"offensive", "defensive"}:
            raise SystemExit(f"unknown v3 rebound subtype action={action}: {subtype!r}")
        team_id = int(r.teamId)
        if team_id not in team_map:
            raise SystemExit(f"missing rebound team mapping action={action} team={team_id}")
        rebound_team = team_map[team_id]
        other = [abbr for tid, abbr in team_map.items() if tid != team_id]
        if len(other) != 1:
            raise SystemExit(f"cannot resolve opponent action={action}")
        opponent = other[0] if subtype == "offensive" else rebound_team

        period = int(old.PERIOD)
        clock = str(old.PCTIMESTRING)
        remain = clock_seconds(clock)
        period_len = 720 if period <= 4 else 300
        start = min(period_len, remain + 1)
        end = max(0, remain - 1)
        tag = action % 90
        synthetic.append({
            "GAMEID": GAME_ID,
            "OPPONENT": opponent,
            "PERIOD": period,
            "STARTTIME": format_clock(start, tag),
            "ENDTIME": format_clock(end, tag),
            "DESCRIPTION": description,
            "OFFENSIVEREBOUNDS": 1 if subtype == "offensive" else 0,
        })
        mapping.append({
            "action_number": action,
            "period": period,
            "clock": clock,
            "subtype": subtype,
            "rebound_team_id": team_id,
            "rebound_team": rebound_team,
            "pbp_opponent": opponent,
            "description": description,
        })

    synth = pd.DataFrame(synthetic)
    for col in pbp.columns:
        if col not in synth:
            synth[col] = pd.NA
    for col in synth.columns:
        if col not in pbp:
            pbp[col] = pd.NA
    synth = synth[pbp.columns]
    combined = pd.concat([pbp, synth], ignore_index=True)
    combined.to_csv(args.pbp, index=False)

    payload = {
        "game_id": GAME_ID,
        "status": "filled_from_nbastatsv3",
        "synthetic_pbp_rows": int(len(synth)),
        "v3_rebound_rows": int(len(rebounds)),
        "legacy_event_matches": int(len(mapping)),
        "team_map": team_map,
        "mapping": mapping,
        "classification_source": "nbastatsv3 subType",
        "lineup_and_live_rebound_source": "legacy nbastats_2009 through normal production engine",
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "mapping"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
