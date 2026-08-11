#!/usr/bin/env python3
"""Fill the one known PBP Stats source gap in 2009-10 from independent NBA feeds.

Game 20900212 is present in the legacy NBA Stats feed and NBA Stats v3 but is
absent from pbpstats_2009.  This adapter does not fabricate rebound events:

- every v3 rebound actionNumber must map one-to-one to a legacy NBA EVENTNUM;
- the historical engine's own event rule determines live/dead rebound status;
- rebound team is cross-checked across legacy/v3 fields;
- offensive vs defensive is derived from rebound team versus the immediately
  preceding missed-shot team;
- for player rebounds, the cumulative ``(Off:X Def:Y)`` counters in the legacy
  description independently audit that classification whenever available.

The resulting PBP-shaped rows use one synthetic possession id per rebound so the
normal production classifier consumes the already-audited OREB/DREB label
without coupling unrelated rebound rows together.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core

GAME_ID = 20900212
PLAYER_MAX = core.PLAYER_MAX
COUNTER_RE = re.compile(r"\(\s*Off\s*:\s*(\d+)\s+Def\s*:\s*(\d+)\s*\)", re.I)


def nba_description(frame: pd.DataFrame) -> pd.Series:
    return core.nba_description(frame)


def clock_seconds(value: object) -> int:
    minute, second = str(value).split(":")[:2]
    return int(minute) * 60 + int(float(second))


def format_clock(seconds: int, tag: int) -> str:
    seconds = max(0, int(seconds))
    minute, second = divmod(seconds, 60)
    return f"{minute:02d}:{second:02d}.{tag % 90 + 10:02d}"


def _int(value: object) -> int:
    if pd.isna(value):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _game_teams(game: pd.DataFrame) -> list[int]:
    values: list[int] = []
    for col in ("PLAYER1_TEAM_ID", "PLAYER2_TEAM_ID", "PLAYER3_TEAM_ID"):
        if col not in game:
            continue
        vals = pd.to_numeric(game[col], errors="coerce").dropna().astype(int)
        values.extend(int(v) for v in vals if v >= PLAYER_MAX)
    counts = pd.Series(values, dtype="int64").value_counts()
    teams = [int(v) for v in counts.head(2).index]
    if len(teams) != 2:
        raise ValueError(f"expected two legacy teams for game {GAME_ID}, got {teams}")
    return teams


def _rebound_team(old: pd.Series, modern: pd.Series, teams: list[int]) -> tuple[int, dict]:
    evidence: dict[str, int] = {}
    for label, value in (
        ("legacy_player1_team", old.get("PLAYER1_TEAM_ID")),
        ("legacy_player1_id", old.get("PLAYER1_ID")),
        ("v3_team_id", modern.get("teamId")),
        ("v3_person_id", modern.get("personId")),
    ):
        value_i = _int(value)
        if value_i in teams:
            evidence[label] = value_i
    unique = set(evidence.values())
    if len(unique) != 1:
        raise ValueError(
            f"cannot uniquely resolve rebound team action={int(old.EVENTNUM)} "
            f"evidence={evidence} teams={teams}"
        )
    return next(iter(unique)), evidence


def _previous_miss_team(game: pd.DataFrame, position: int) -> tuple[int, dict]:
    period = int(game.iloc[position].PERIOD)
    for scan in range(position - 1, -1, -1):
        row = game.iloc[scan]
        if int(row.PERIOD) != period:
            break
        event_type = int(row.EVENTMSGTYPE)
        desc = str(row.DESCRIPTION_NORM).lower()
        if event_type == 2 or (event_type == 3 and "miss" in desc):
            team = _int(row.get("PLAYER1_TEAM_ID"))
            if team <= 0:
                raise ValueError(
                    f"missed shot has no shooting team before rebound action={int(game.iloc[position].EVENTNUM)} "
                    f"shot_event={int(row.EVENTNUM)}"
                )
            return team, {
                "shot_event": int(row.EVENTNUM),
                "shot_event_type": event_type,
                "shot_team_id": team,
                "shot_description": str(row.DESCRIPTION_NORM),
            }
    raise ValueError(
        f"no prior missed shot found for live rebound action={int(game.iloc[position].EVENTNUM)}"
    )


def _counter_classification(description: str, player: int, previous: dict[int, tuple[int, int]]) -> tuple[str | None, dict | None]:
    match = COUNTER_RE.search(description)
    if not match or not (0 < player < PLAYER_MAX):
        return None, None
    current = (int(match.group(1)), int(match.group(2)))
    before = previous.get(player, (0, 0))
    previous[player] = current
    d_off = current[0] - before[0]
    d_def = current[1] - before[1]
    detail = {
        "player_id": player,
        "previous_off_def": list(before),
        "current_off_def": list(current),
        "delta_off": d_off,
        "delta_def": d_def,
    }
    if (d_off, d_def) == (1, 0):
        return "offensive", detail
    if (d_off, d_def) == (0, 1):
        return "defensive", detail
    # The first available counter for a player can legitimately jump if an
    # earlier rebound description did not contain counters. Treat it as audit
    # unavailable rather than inventing a label.
    return None, detail


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
            payload = {
                "game_id": GAME_ID,
                "status": "already_present",
                "existing_rows": int(existing.sum()),
            }
            args.audit.parent.mkdir(parents=True, exist_ok=True)
            args.audit.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0

    ng = nba[pd.to_numeric(nba.GAME_ID, errors="coerce").eq(GAME_ID)].copy()
    vg = v3[pd.to_numeric(v3.gameId, errors="coerce").eq(GAME_ID)].copy()
    if ng.empty or vg.empty:
        raise SystemExit(f"fallback source missing: legacy_nba={len(ng)} v3={len(vg)}")

    ng["EVENTNUM"] = pd.to_numeric(ng.EVENTNUM, errors="raise").astype(int)
    ng["PERIOD"] = pd.to_numeric(ng.PERIOD, errors="raise").astype(int)
    ng["DESCRIPTION_NORM"] = nba_description(ng)
    ng["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(ng.PERIOD, ng.PCTIMESTRING)]
    ng = ng.sort_values(["PERIOD", "EVENTNUM"], kind="stable").reset_index(drop=True)

    vg["actionNumber"] = pd.to_numeric(vg.actionNumber, errors="raise").astype(int)
    vg["action_norm"] = vg.actionType.astype("string").fillna("").str.strip().str.lower()
    rebounds = vg[vg.action_norm.eq("rebound")].sort_values("actionNumber", kind="stable").copy()
    legacy_rebounds = ng[ng.EVENTMSGTYPE.eq(4)].copy()
    if rebounds.empty:
        raise SystemExit("v3 fallback game has no rebound actions")

    modern_actions = set(rebounds.actionNumber.astype(int))
    legacy_actions = set(legacy_rebounds.EVENTNUM.astype(int))
    if modern_actions != legacy_actions:
        raise SystemExit(
            f"v3/legacy rebound action sets differ: "
            f"v3_only={sorted(modern_actions-legacy_actions)} "
            f"legacy_only={sorted(legacy_actions-modern_actions)}"
        )

    by_event = {int(row.EVENTNUM): (idx, row) for idx, row in ng.iterrows() if int(row.EVENTMSGTYPE) == 4}
    modern_by_event = {int(row.actionNumber): row for _, row in rebounds.iterrows()}
    teams = _game_teams(ng)

    team_pairs = (
        vg[["teamId", "teamTricode"]]
        .dropna(subset=["teamId", "teamTricode"])
        .assign(teamId=lambda x: pd.to_numeric(x.teamId, errors="coerce"))
        .dropna()
        .drop_duplicates()
    )
    team_map = {
        int(r.teamId): str(r.teamTricode)
        for r in team_pairs.itertuples(index=False)
        if int(r.teamId) in teams
    }
    for team in teams:
        team_map.setdefault(team, str(team))

    counter_previous: dict[int, tuple[int, int]] = {}
    synthetic = []
    mapping = []
    live_count = 0
    dead_count = 0
    counter_audits = 0

    for action in sorted(legacy_actions):
        position, old = by_event[action]
        modern = modern_by_event[action]
        description = str(old.DESCRIPTION_NORM)
        rebound_team_id, team_evidence = _rebound_team(old, modern, teams)
        real = bool(core._nba_real_rebound(ng, position))
        player = _int(old.get("PLAYER1_ID"))
        counter_label, counter_detail = _counter_classification(description, player, counter_previous)

        if real:
            live_count += 1
            shot_team_id, shot_evidence = _previous_miss_team(ng, position)
            offensive = rebound_team_id == shot_team_id
            derived_label = "offensive" if offensive else "defensive"
            if counter_label is not None:
                counter_audits += 1
                if counter_label != derived_label:
                    raise SystemExit(
                        f"player counter audit disagrees action={action}: "
                        f"derived={derived_label} counter={counter_label} "
                        f"detail={counter_detail} shot={shot_evidence}"
                    )
        else:
            dead_count += 1
            offensive = False
            derived_label = "dead"
            shot_evidence = None

        other_team = teams[0] if rebound_team_id == teams[1] else teams[1]
        rebound_team = team_map[rebound_team_id]
        other_abbr = team_map[other_team]
        # Match the existing PBP Stats convention used by the production join:
        # OREB possession opponent is the non-rebounding team; DREB opponent is
        # the rebounder's team. Dead rows are filtered before TREB aggregation.
        opponent = other_abbr if offensive else rebound_team

        period = int(old.PERIOD)
        clock = str(old.PCTIMESTRING)
        remain = clock_seconds(clock)
        period_len = 720 if period <= 4 else 300
        start = min(period_len, remain + 1)
        end = max(0, remain - 1)
        tag = action % 90
        row = {
            "GAMEID": GAME_ID,
            "OPPONENT": opponent,
            "PERIOD": period,
            "STARTTIME": format_clock(start, tag),
            "ENDTIME": format_clock(end, tag),
            "DESCRIPTION": description,
            "OFFENSIVEREBOUNDS": 1 if offensive else 0,
            "POSSESSION_ID": f"fallback-{GAME_ID}-{action}",
        }
        synthetic.append(row)
        mapping.append(
            {
                "action_number": action,
                "period": period,
                "clock": clock,
                "is_real_rebound": real,
                "derived_classification": derived_label,
                "rebound_team_id": rebound_team_id,
                "rebound_team": rebound_team,
                "team_evidence": team_evidence,
                "shot_evidence": shot_evidence,
                "counter_audit": counter_detail,
                "counter_label": counter_label,
                "v3_subtype": str(modern.get("subType", "")),
                "pbp_opponent": opponent,
                "description": description,
            }
        )

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
        "status": "filled_from_legacy_plus_nbastatsv3",
        "synthetic_pbp_rows": int(len(synth)),
        "v3_rebound_rows": int(len(rebounds)),
        "legacy_rebound_rows": int(len(legacy_rebounds)),
        "legacy_event_matches": int(len(mapping)),
        "live_rebounds": live_count,
        "dead_rebounds": dead_count,
        "player_counter_audits": counter_audits,
        "teams": teams,
        "team_map": team_map,
        "mapping": mapping,
        "classification_source": "legacy live/dead rule + rebound-team versus prior missed-shot team",
        "independent_event_source": "nbastatsv3 actionNumber/team evidence",
        "player_classification_audit": "legacy cumulative Off/Def counters where increment is unambiguous",
        "lineup_source": "legacy nbastats_2009 through normal production engine",
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "mapping"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
