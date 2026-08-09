from __future__ import annotations

import gzip
import json
import math
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import nba_on_court as noc

BASE = Path(__file__).resolve().parent
CORE_ROOT = BASE / "impact_database" / "core_checkpoints"
OUT = BASE / "impact_database" / "local_pbp_canary_result.json"
SEASON = "2021-22"
START_YEAR = 2021
TEAM_ID = 1610612745  # Houston; normal 82-game season and completed core checkpoint
MAX_PLAYERS = 6
MINUTES_TOL = 0.05


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def norm(v: Any) -> str:
    text = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def norm_game_id(v: Any) -> str:
    s = str(v or "").strip()
    if not s or s.lower() == "nan":
        return ""
    try:
        if "." in s:
            s = str(int(float(s)))
    except Exception:
        pass
    digits = "".join(ch for ch in s if ch.isdigit())
    return (digits.lstrip("0") or "0") if digits else ""


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        d = json.load(handle)
    return d if isinstance(d, dict) else {}


def team_ids_in_game(game: pd.DataFrame) -> set[int]:
    ids: set[int] = set()
    for col in ("PLAYER1_TEAM_ID", "PLAYER2_TEAM_ID", "PLAYER3_TEAM_ID"):
        if col not in game.columns:
            continue
        for v in game[col].dropna().unique():
            x = finite(v)
            if x is not None and x >= 1610612737:
                ids.add(int(x))
    return ids


def lineup_columns(df: pd.DataFrame) -> list[str]:
    cols = [f"AWAY_PLAYER{i}" for i in range(1, 6)] + [f"HOME_PLAYER{i}" for i in range(1, 6)]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"nba-on-court did not produce lineup columns: {missing}")
    return cols


def game_duration_seconds(df: pd.DataFrame) -> float:
    max_period = int(pd.to_numeric(df["PERIOD"], errors="coerce").max())
    return 2880.0 + max(0, max_period - 4) * 300.0


def minutes_for_players(df: pd.DataFrame, player_ids: set[int]) -> dict[int, float]:
    g = df.sort_values(["PERIOD", "EVENTNUM"], kind="stable").reset_index(drop=True)
    cols = lineup_columns(g)
    elapsed = pd.to_numeric(g["PCTIMESTRING"], errors="coerce").ffill().fillna(0).astype(float).to_numpy()
    duration = game_duration_seconds(g)
    totals = {pid: 0.0 for pid in player_ids}

    # nba-on-court converts PCTIMESTRING to elapsed seconds from game start and
    # updates lineup columns at substitution events. Attribute each interval after
    # an event to the lineup shown on that event; the interval before the first
    # event belongs to the first observed lineup.
    for i in range(len(g)):
        start = max(0.0, min(duration, float(elapsed[i])))
        end = float(elapsed[i + 1]) if i + 1 < len(g) else duration
        end = max(start, min(duration, end))
        delta = end - start
        if delta <= 0:
            continue
        on = set()
        for c in cols:
            x = finite(g.at[i, c])
            if x is not None:
                on.add(int(x))
        for pid in player_ids & on:
            totals[pid] += delta

    if len(g) and elapsed[0] > 0:
        pre = min(duration, float(elapsed[0]))
        on0 = set()
        for c in cols:
            x = finite(g.at[0, c])
            if x is not None:
                on0.add(int(x))
        for pid in player_ids & on0:
            totals[pid] += pre

    return {pid: sec / 60.0 for pid, sec in totals.items()}


def player_identity(row: dict[str, Any]) -> tuple[int | None, str]:
    raw = row.get("EntityId") or row.get("RowId") or row.get("PlayerId")
    try:
        pid = int(float(str(raw)))
    except Exception:
        pid = None
    name = str(row.get("Name") or row.get("ShortName") or "").strip()
    return pid, name


def expected_minutes_map(core: dict[str, Any]) -> dict[str, float]:
    results = core.get("team_on_off_results")
    if not isinstance(results, dict):
        return {}
    # MinutesOn is repeated across metrics. Use the first metric that has broad coverage.
    best: dict[str, float] = {}
    for _, rows in results.items():
        if not isinstance(rows, list):
            continue
        current: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = norm(row.get("Name"))
            minutes = finite(row.get("MinutesOn"))
            if name and minutes is not None:
                current[name] = minutes
        if len(current) > len(best):
            best = current
    return best


def main() -> int:
    checkpoint = CORE_ROOT / SEASON / f"{TEAM_ID}.json.gz"
    core = read_gzip_json(checkpoint)
    if core.get("complete") is not True or core.get("absent_team_season") is True:
        raise RuntimeError(f"core checkpoint is not complete: {checkpoint}")

    expected_by_name = expected_minutes_map(core)
    candidates = []
    for row in core.get("player_totals") or []:
        if not isinstance(row, dict):
            continue
        pid, name = player_identity(row)
        seconds = finite(row.get("SecondsPlayed"))
        if pid is None or not name or seconds is None or seconds <= 0:
            continue
        if norm(name) not in expected_by_name:
            continue
        candidates.append((seconds, pid, name, expected_by_name[norm(name)]))
    candidates.sort(reverse=True)
    selected = candidates[:MAX_PLAYERS]
    if len(selected) < 3:
        raise RuntimeError(f"insufficient core players for canary: {len(selected)}")

    selected_ids = {pid for _, pid, _, _ in selected}
    print(f"LOCAL-PBP CORE CANARY: {SEASON} team={TEAM_ID} players={len(selected)}", flush=True)
    print("Downloading one public bulk nbastats season; zero PBP Stats API calls...", flush=True)
    nbastats = noc.load_nba_data(
        seasons=START_YEAR,
        data="nbastats",
        seasontype="rg",
        in_memory=True,
        use_pandas=True,
    )
    if not isinstance(nbastats, pd.DataFrame) or nbastats.empty:
        raise RuntimeError("nbastats bulk season download returned no dataframe")
    nbastats.columns = [str(c).upper() for c in nbastats.columns]
    if "GAME_ID" not in nbastats.columns:
        raise RuntimeError(f"nbastats bulk data missing GAME_ID: {list(nbastats.columns)}")
    nbastats["_GAME_KEY"] = nbastats["GAME_ID"].map(norm_game_id)

    team_games: list[tuple[str, pd.DataFrame]] = []
    for gid, game in nbastats.groupby("_GAME_KEY", sort=False):
        if gid and TEAM_ID in team_ids_in_game(game):
            team_games.append((gid, game.copy().reset_index(drop=True)))
    if len(team_games) < 70:
        raise RuntimeError(f"bulk season found too few team games: {len(team_games)}")

    totals = {pid: 0.0 for pid in selected_ids}
    unresolved = []
    for idx, (gid, raw) in enumerate(team_games, 1):
        try:
            # retry=0 prevents any fallback network call to stats.nba.com.
            lined = noc.players_on_court(raw, retry=0)
            mins = minutes_for_players(lined, selected_ids)
            for pid, value in mins.items():
                totals[pid] += value
        except Exception as exc:
            unresolved.append({"game_id": gid, "error": repr(exc)[:500]})
        if idx % 10 == 0 or idx == len(team_games):
            print(f"processed_games={idx}/{len(team_games)} unresolved={len(unresolved)}", flush=True)

    players = []
    all_minutes_match = True
    for _, pid, name, expected in selected:
        actual = totals.get(pid, 0.0)
        diff = actual - expected
        matched = abs(diff) <= MINUTES_TOL
        all_minutes_match = all_minutes_match and matched
        players.append({
            "player_id": pid,
            "player": name,
            "expected_minutes_on": expected,
            "local_minutes_on": round(actual, 6),
            "diff_minutes": round(diff, 6),
            "within_0_05_minutes": matched,
        })
        print(json.dumps(players[-1]), flush=True)

    passed = bool(len(unresolved) == 0 and all_minutes_match)
    result = {
        "passed": passed,
        "validation": "bulk nbastats + nba-on-court local lineup reconstruction versus accepted completed 780/780 core MinutesOn",
        "season": SEASON,
        "start_year": START_YEAR,
        "team_id": TEAM_ID,
        "team_games_found": len(team_games),
        "players_checked": len(players),
        "minutes_tolerance": MINUTES_TOL,
        "unresolved_games": unresolved,
        "players": players,
        "pbp_stats_api_calls": 0,
        "production_stage2_cache_modified": False,
    }
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
