from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import nba_on_court as noc

BASE = Path(__file__).resolve().parent
CACHE = BASE / "impact_database" / "corrected_off" / "cache"
OUT = BASE / "impact_database" / "local_pbp_canary_result.json"
SEASON = "2007-08"
START_YEAR = 2007
MAX_WINDOWS = 6


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


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
    return digits.lstrip("0") or "0" if digits else ""


def norm_date(v: Any) -> str:
    s = str(v or "").strip()
    if not s or s.lower() == "nan":
        return ""
    try:
        return pd.to_datetime(s).date().isoformat()
    except Exception:
        return ""


def read_cache(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        d = json.load(handle)
    return d if isinstance(d, dict) else {}


def completed_canaries() -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(CACHE.glob(f"{SEASON}__*.json.gz")):
        try:
            d = read_cache(path)
        except Exception:
            continue
        if d.get("complete") is not True:
            continue
        start = str(d.get("query_start_date") or "")
        end = str(d.get("query_end_date") or "")
        if not start or not end or start >= end:
            continue
        metrics = d.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            continue
        by_metric = {str(m.get("metric") or ""): m for m in metrics if isinstance(m, dict)}
        if "OffRebounds" not in by_metric or "DefRebounds" not in by_metric:
            continue
        # Prefer windows with at least one expected team game and some focal minutes.
        if int(d.get("team_games_in_window") or 0) < 1:
            continue
        if float(d.get("minutes_on") or 0) <= 0:
            continue
        rows.append((path, d))
    # Spread across teams/date lengths rather than accidentally selecting near-duplicates.
    selected: list[tuple[Path, dict[str, Any]]] = []
    seen_teams: set[int] = set()
    for item in rows:
        team = int(item[1].get("team_id") or 0)
        if team and team not in seen_teams:
            selected.append(item); seen_teams.add(team)
        if len(selected) >= MAX_WINDOWS:
            break
    if len(selected) < MAX_WINDOWS:
        for item in rows:
            if item not in selected:
                selected.append(item)
            if len(selected) >= MAX_WINDOWS:
                break
    return selected


def game_date_map(pbpstats: pd.DataFrame) -> dict[str, str]:
    p = pbpstats.copy()
    p.columns = [str(c).upper() for c in p.columns]
    if "GAMEID" not in p.columns or "GAMEDATE" not in p.columns:
        raise RuntimeError(f"pbpstats bulk data missing GAMEID/GAMEDATE: {list(p.columns)}")
    out: dict[str, str] = {}
    for gid, gd in zip(p["GAMEID"], p["GAMEDATE"]):
        key = norm_game_id(gid); day = norm_date(gd)
        if key and day:
            out[key] = day
    return out


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


def side_team_ids(game: pd.DataFrame) -> tuple[int | None, int | None]:
    """Return (away, home), using StatsNBA PERSON type convention used by nba-on-court."""
    candidates: dict[int, list[int]] = {5: [], 4: []}
    for n in (1, 2, 3):
        pcol = f"PERSON{n}TYPE"; tcol = f"PLAYER{n}_TEAM_ID"
        if pcol not in game.columns or tcol not in game.columns:
            continue
        for ptype, tid in zip(game[pcol], game[tcol]):
            p = finite(ptype); t = finite(tid)
            if p is None or t is None or int(p) not in candidates or t < 1610612737:
                continue
            candidates[int(p)].append(int(t))
    def mode(values: list[int]) -> int | None:
        return Counter(values).most_common(1)[0][0] if values else None
    return mode(candidates[5]), mode(candidates[4])


def event_rebound_team(row: pd.Series, away_team: int | None, home_team: int | None) -> int | None:
    tid = finite(row.get("PLAYER1_TEAM_ID"))
    if tid is not None and tid >= 1610612737:
        return int(tid)
    # Team rebounds can have no player ID/team ID. Use the side description only for
    # EVENTMSGTYPE=4, where exactly one side owns the rebound event.
    home_desc = str(row.get("HOMEDESCRIPTION") or "").strip()
    away_desc = str(row.get("VISITORDESCRIPTION") or "").strip()
    if home_desc and not away_desc and home_team:
        return home_team
    if away_desc and not home_desc and away_team:
        return away_team
    return None


def lineup_columns(df: pd.DataFrame) -> list[str]:
    cols = [f"AWAY_PLAYER{i}" for i in range(1, 6)] + [f"HOME_PLAYER{i}" for i in range(1, 6)]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"nba-on-court did not produce lineup columns: {missing}")
    return cols


def player_on_mask(df: pd.DataFrame, player_id: int) -> pd.Series:
    cols = lineup_columns(df)
    return df[cols].apply(lambda row: any(finite(v) is not None and int(float(v)) == player_id for v in row), axis=1)


def game_minutes(df: pd.DataFrame, player_id: int) -> tuple[float, float]:
    g = df.sort_values(["PERIOD", "EVENTNUM"], kind="stable").reset_index(drop=True)
    elapsed = pd.to_numeric(g["PCTIMESTRING"], errors="coerce").fillna(method="ffill").fillna(0).astype(float).to_numpy()
    max_period = int(pd.to_numeric(g["PERIOD"], errors="coerce").max())
    duration = 2880.0 + max(0, max_period - 4) * 300.0
    on = player_on_mask(g, player_id).to_numpy(dtype=bool)
    total_on = 0.0
    if len(g):
        if elapsed[0] > 0 and on[0]:
            total_on += elapsed[0]
        for i in range(len(g)):
            nxt = elapsed[i + 1] if i + 1 < len(g) else duration
            delta = max(0.0, min(duration, nxt) - max(0.0, elapsed[i]))
            if on[i]:
                total_on += delta
    total_on = min(max(total_on, 0.0), duration)
    return total_on / 60.0, (duration - total_on) / 60.0


def expected_metrics(cache: dict[str, Any]) -> dict[str, Any]:
    by_metric = {str(m.get("metric") or ""): m for m in cache.get("metrics", []) if isinstance(m, dict)}
    o = by_metric.get("OffRebounds") or {}
    d = by_metric.get("DefRebounds") or {}
    expected_team_reb = None
    if finite(o.get("on")) is not None and finite(d.get("on")) is not None:
        expected_team_reb = float(o["on"]) + float(d["on"])
    pct = by_metric.get("ReboundPct") or by_metric.get("ReboundsPct") or by_metric.get("ReboundPercentage") or {}
    rebound_names = sorted(k for k in by_metric if "rebound" in k.casefold())
    return {
        "team_rebounds_on": expected_team_reb,
        "rebound_pct_on": finite(pct.get("on")),
        "metric_names_containing_rebound": rebound_names,
    }


def calculate_window(nbastats: pd.DataFrame, dates: dict[str, str], cache_path: Path, cache: dict[str, Any]) -> dict[str, Any]:
    team_id = int(cache["team_id"]); player_id = int(str(cache["player_id"]).split(".")[0])
    start = str(cache["query_start_date"]); end = str(cache["query_end_date"])
    game_ids: list[str] = []
    for gid, game in nbastats.groupby("_GAME_KEY", sort=False):
        day = dates.get(gid, "")
        if day and start <= day <= end and team_id in team_ids_in_game(game):
            game_ids.append(gid)

    team_rebounds_on = 0
    opp_rebounds_on = 0
    unknown_rebounds_on = 0
    minutes_on = 0.0
    minutes_off = 0.0
    unresolved_games: list[dict[str, Any]] = []

    for gid in game_ids:
        raw = nbastats.loc[nbastats["_GAME_KEY"] == gid].copy().reset_index(drop=True)
        away_team, home_team = side_team_ids(raw)
        try:
            # retry=0 guarantees this canary never falls back to stats.nba.com boxscore calls.
            lined = noc.players_on_court(raw, retry=0)
        except Exception as exc:
            unresolved_games.append({"game_id": gid, "date": dates.get(gid), "error": repr(exc)[:500]})
            continue
        on_mask = player_on_mask(lined, player_id)
        rebounds = lined.loc[(pd.to_numeric(lined["EVENTMSGTYPE"], errors="coerce") == 4) & on_mask]
        for _, row in rebounds.iterrows():
            rteam = event_rebound_team(row, away_team, home_team)
            if rteam is None:
                unknown_rebounds_on += 1
            elif rteam == team_id:
                team_rebounds_on += 1
            else:
                opp_rebounds_on += 1
        on_min, off_min = game_minutes(lined, player_id)
        minutes_on += on_min; minutes_off += off_min

    raw_pct = None
    if team_rebounds_on + opp_rebounds_on > 0:
        raw_pct = team_rebounds_on / (team_rebounds_on + opp_rebounds_on)
    expected = expected_metrics(cache)
    exp_reb = finite(expected.get("team_rebounds_on"))
    exp_pct = finite(expected.get("rebound_pct_on"))
    exp_on = finite(cache.get("minutes_on")); exp_off = finite(cache.get("minutes_off"))
    expected_games = int(cache.get("team_games_in_window") or 0)

    rebound_exact = exp_reb is not None and abs(team_rebounds_on - exp_reb) < 1e-9
    pct_close = exp_pct is None or (raw_pct is not None and abs(raw_pct - exp_pct) <= 0.00055)
    minutes_on_close = exp_on is not None and abs(minutes_on - exp_on) <= 0.05
    minutes_off_close = exp_off is not None and abs(minutes_off - exp_off) <= 0.05
    game_count_exact = len(game_ids) == expected_games
    passed = bool(
        game_count_exact and not unresolved_games and unknown_rebounds_on == 0
        and rebound_exact and pct_close and minutes_on_close and minutes_off_close
    )
    return {
        "cache_file": cache_path.name,
        "season": cache.get("season"), "team_id": team_id, "player_id": player_id, "player": cache.get("player"),
        "query_start_date": start, "query_end_date": end,
        "expected_team_games": expected_games, "bulk_games_found": len(game_ids), "game_ids": game_ids,
        "unresolved_lineup_games": unresolved_games,
        "raw_team_rebounds_on": team_rebounds_on, "expected_team_rebounds_on": exp_reb,
        "raw_opponent_rebounds_on": opp_rebounds_on, "unknown_rebounds_on": unknown_rebounds_on,
        "raw_rebound_pct_on": raw_pct, "expected_rebound_pct_on": exp_pct,
        "raw_minutes_on": round(minutes_on, 6), "expected_minutes_on": exp_on,
        "raw_minutes_off": round(minutes_off, 6), "expected_minutes_off": exp_off,
        "diff_minutes_on": None if exp_on is None else round(minutes_on - exp_on, 6),
        "diff_minutes_off": None if exp_off is None else round(minutes_off - exp_off, 6),
        "rebound_metric_names": expected["metric_names_containing_rebound"],
        "checks": {
            "game_count_exact": game_count_exact,
            "all_lineups_local_only_resolved": not unresolved_games,
            "unknown_rebound_events_zero": unknown_rebounds_on == 0,
            "team_rebound_count_exact": rebound_exact,
            "rebound_pct_within_display_rounding": pct_close,
            "minutes_on_within_0_05": minutes_on_close,
            "minutes_off_within_0_05": minutes_off_close,
        },
        "passed": passed,
    }


def main() -> int:
    canaries = completed_canaries()
    if len(canaries) < 3:
        raise RuntimeError(f"need >=3 completed {SEASON} partial-tenure caches, found {len(canaries)}")

    print(f"Downloading public bulk NBA PBP for {SEASON}; no PBP Stats API calls...", flush=True)
    nbastats = noc.load_nba_data(seasons=START_YEAR, data="nbastats", seasontype="rg", in_memory=True, use_pandas=True)
    pbpstats = noc.load_nba_data(seasons=START_YEAR, data="pbpstats", seasontype="rg", in_memory=True, use_pandas=True)
    if not isinstance(nbastats, pd.DataFrame) or nbastats.empty:
        raise RuntimeError("nbastats bulk season download returned no dataframe")
    if not isinstance(pbpstats, pd.DataFrame) or pbpstats.empty:
        raise RuntimeError("pbpstats bulk season download returned no dataframe")
    nbastats.columns = [str(c).upper() for c in nbastats.columns]
    if "GAME_ID" not in nbastats.columns:
        raise RuntimeError(f"nbastats bulk data missing GAME_ID: {list(nbastats.columns)}")
    nbastats["_GAME_KEY"] = nbastats["GAME_ID"].map(norm_game_id)
    dates = game_date_map(pbpstats)

    results = []
    for path, cache in canaries:
        result = calculate_window(nbastats, dates, path, cache)
        results.append(result)
        print(json.dumps({
            "cache": path.name,
            "passed": result["passed"],
            "games": [result["bulk_games_found"], result["expected_team_games"]],
            "team_rebounds": [result["raw_team_rebounds_on"], result["expected_team_rebounds_on"]],
            "minutes_on": [result["raw_minutes_on"], result["expected_minutes_on"]],
            "unresolved": len(result["unresolved_lineup_games"]),
        }), flush=True)

    passed = sum(1 for r in results if r["passed"])
    summary = {
        "generated_by": "local_pbp_treb_canary.py",
        "production_api_calls": 0,
        "external_source": "public shufinskiy/nba_data bulk season archives via nba-on-court",
        "season": SEASON,
        "canary_windows": len(results),
        "passed_windows": passed,
        "failed_windows": len(results) - passed,
        "all_passed": passed == len(results),
        "purpose": "Validate exact tenure-date team TREB%, rebound counts, and on/off minutes from bulk play-by-play before any production replacement of Stage2 API collection.",
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("production_api_calls", "canary_windows", "passed_windows", "failed_windows", "all_passed")}, indent=2), flush=True)
    return 0 if summary["all_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
