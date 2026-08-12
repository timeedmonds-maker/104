#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

BLOCKER_GAMES = {
    20201160, 20400335, 20600887, 20700319, 20800142, 21100842,
    21500916, 21800143, 22000485, 21901316, 21901317, 21901318,
}

# Candidate pools emitted by the V3/team-local canary.  The CC0 source is used
# as independent full-game minute evidence only; `startingPosition` is retained
# in the audit but is NOT treated as a quarter-starter flag.
AMBIGUITIES = {
    20201160: {"period": 5, "team_id": 1610612765, "candidates": [1088, 1442, 1888]},
    20400335: {"period": 2, "team_id": 1610612740, "candidates": [1924, 2365, 2424, 2437, 2454, 2747]},
    20600887: {"period": 5, "team_id": 1610612750, "candidates": [1536, 2033]},
    20700319: {"period": 4, "team_id": 1610612752, "candidates": [255, 1897, 2756, 101181]},
    20800142: {"period": 5, "team_id": 1610612752, "candidates": [2216, 200776]},
    21100842: {"period": 2, "team_id": 1610612766, "candidates": [2550, 2736]},
    21500916: {"period": 5, "team_id": 1610612757, "candidates": [202334, 203148, 203459, 203943, 203994, 1626145, 1626192, 1626242]},
    21800143: {"period": 6, "team_id": 1610612741, "candidates": [202703, 203487, 203200, 1627885]},
    22000485: {"period": 1, "team_id": 1610612742, "candidates": [1628973, 1630179, 201144]},
}


def pick(columns, names):
    lower = {str(c).lower(): c for c in columns}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def numeric_game_id(value):
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def parse_minutes(raw: pd.Series) -> pd.Series:
    num = pd.to_numeric(raw, errors="coerce")
    if num.notna().sum() >= len(raw) * 0.5:
        return num

    def parse(value):
        s = str(value).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        if ":" in s:
            try:
                m, sec = s.split(":", 1)
                return float(m) + float(sec) / 60.0
            except Exception:
                return None
        try:
            return float(s)
        except Exception:
            return None

    return raw.map(parse)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    d = pd.read_csv(a.csv, low_memory=False)
    cols = list(d.columns)
    game = pick(cols, ["gameId", "game_id", "GAME_ID"])
    player = pick(cols, ["personId", "playerId", "player_id", "PERSON_ID", "PLAYER_ID"])
    team = pick(cols, ["playerteamId", "playerTeamId", "teamId", "team_id", "TEAM_ID"])
    minutes = pick(cols, ["numMinutes", "minutes", "min", "MIN"])
    date = pick(cols, ["gameDateTimeEst", "gameDateTime", "gameDate", "game_date", "GAME_DATE"])
    name = pick(cols, ["playerName", "player_name", "name", "PLAYER_NAME"])
    first = pick(cols, ["firstName", "first_name", "FIRST_NAME"])
    last = pick(cols, ["lastName", "last_name", "LAST_NAME"])
    season = pick(cols, ["season", "seasonId", "season_id", "SEASON_ID"])
    starter = pick(cols, ["startingPosition", "startPosition", "START_POSITION", "starter"])
    game_type = pick(cols, ["gameType", "game_type", "SEASON_TYPE"])

    payload = {
        "row_count": int(len(d)),
        "columns": cols,
        "resolved_columns": {
            "game": game, "player": player, "team": team, "minutes": minutes,
            "date": date, "name": name, "first": first, "last": last,
            "season": season, "starter": starter, "game_type": game_type,
        },
        "duplicate_game_player_rows": None,
        "zero_or_blank_minute_rows": None,
        "positive_minute_rows": None,
        "blocker_game_ids_requested": sorted(BLOCKER_GAMES),
        "blocker_game_ids_found": [],
        "blocker_game_ids_missing": [],
        "blocker_rows": [],
        "ambiguity_evidence": [],
        "sample_zero_or_blank_minute_rows": [],
        "sample_positive_rows": [],
    }

    if game and player:
        payload["duplicate_game_player_rows"] = int(d.duplicated([game, player], keep=False).sum())

    minute_num = None
    if minutes:
        minute_num = parse_minutes(d[minutes])
        blank = minute_num.isna() | minute_num.le(0)
        payload["zero_or_blank_minute_rows"] = int(blank.sum())
        payload["positive_minute_rows"] = int((~blank).sum())
        keep = [c for c in (game, date, season, team, player, name, first, last, starter, game_type, minutes) if c]
        zero_sample = d.loc[blank, keep].head(20)
        pos_sample = d.loc[~blank, keep].head(5)
        payload["sample_zero_or_blank_minute_rows"] = zero_sample.where(pd.notna(zero_sample), None).to_dict("records")
        payload["sample_positive_rows"] = pos_sample.where(pd.notna(pos_sample), None).to_dict("records")

    if game:
        normalized_game = d[game].map(numeric_game_id)
        blocker_mask = normalized_game.isin(BLOCKER_GAMES)
        found = sorted({int(x) for x in normalized_game[blocker_mask].dropna().tolist()})
        payload["blocker_game_ids_found"] = found
        payload["blocker_game_ids_missing"] = sorted(BLOCKER_GAMES - set(found))

        keep = [c for c in (game, date, game_type, season, team, player, name, first, last, starter, minutes) if c]
        b = d.loc[blocker_mask, keep].copy()
        b.insert(0, "normalized_game_id", normalized_game.loc[blocker_mask].astype("int64").values)
        if minute_num is not None:
            b["parsed_minutes"] = minute_num.loc[blocker_mask].values
            b["parsed_seconds"] = (b["parsed_minutes"] * 60.0).round(3)
        sort_cols = [c for c in ["normalized_game_id", team, player] if c in b.columns]
        if sort_cols:
            b = b.sort_values(sort_cols, kind="stable")
        payload["blocker_rows"] = b.where(pd.notna(b), None).to_dict("records")

        if player and team:
            player_num = pd.to_numeric(d[player], errors="coerce")
            team_num = pd.to_numeric(d[team], errors="coerce")
            for gid, spec in sorted(AMBIGUITIES.items()):
                mask = normalized_game.eq(gid) & team_num.eq(spec["team_id"])
                team_rows = d.loc[mask].copy()
                team_minutes = minute_num.loc[mask] if minute_num is not None else pd.Series(index=team_rows.index, dtype=float)
                candidate_mask = mask & player_num.isin(spec["candidates"])
                candidate_rows = d.loc[candidate_mask, [c for c in (player, first, last, minutes, starter) if c]].copy()
                if minute_num is not None:
                    candidate_rows["parsed_minutes"] = minute_num.loc[candidate_mask].values
                    candidate_rows["parsed_seconds"] = (candidate_rows["parsed_minutes"] * 60.0).round(3)
                payload["ambiguity_evidence"].append({
                    "game_id": gid,
                    "period": spec["period"],
                    "team_id": spec["team_id"],
                    "candidate_ids": spec["candidates"],
                    "candidate_rows": candidate_rows.where(pd.notna(candidate_rows), None).to_dict("records"),
                    "team_positive_minutes_sum": float(team_minutes.fillna(0).clip(lower=0).sum()) if minute_num is not None else None,
                    "note": "startingPosition retained as source metadata only; resolution is based on full-game minute fit plus event legality",
                })

    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps({
        "row_count": payload["row_count"],
        "resolved_columns": payload["resolved_columns"],
        "blocker_game_ids_found": payload["blocker_game_ids_found"],
        "blocker_game_ids_missing": payload["blocker_game_ids_missing"],
        "blocker_rows": len(payload["blocker_rows"]),
        "ambiguity_evidence_rows": len(payload["ambiguity_evidence"]),
    }, indent=2))
    if payload["blocker_game_ids_missing"]:
        raise SystemExit("CC0 dataset is missing one or more blocker games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
