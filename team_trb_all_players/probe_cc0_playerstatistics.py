#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

# Nine still-ambiguous historical lineup games plus the three 2019 games for
# which the legacy source pair was missing.  These IDs are stored without the
# leading NBA API "00" prefix; normalize source IDs numerically before match.
BLOCKER_GAMES = {
    20201160, 20400335, 20600887, 20700319, 20800142, 21100842,
    21500916, 21800143, 22000485, 21901316, 21901317, 21901318,
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
    # Historical NBA dataset names this playerteamId.  Keep generic aliases as
    # fallbacks so the probe remains useful if a future snapshot renames it.
    team = pick(cols, ["playerteamId", "playerTeamId", "teamId", "team_id", "TEAM_ID"])
    minutes = pick(cols, ["numMinutes", "minutes", "min", "MIN"])
    date = pick(cols, ["gameDateTime", "gameDate", "game_date", "GAME_DATE"])
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

    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps({
        "row_count": payload["row_count"],
        "resolved_columns": payload["resolved_columns"],
        "blocker_game_ids_found": payload["blocker_game_ids_found"],
        "blocker_game_ids_missing": payload["blocker_game_ids_missing"],
        "blocker_rows": len(payload["blocker_rows"]),
    }, indent=2))
    if payload["blocker_game_ids_missing"]:
        raise SystemExit("CC0 dataset is missing one or more blocker games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
