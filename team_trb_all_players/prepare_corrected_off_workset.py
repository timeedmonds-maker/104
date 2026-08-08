from __future__ import annotations

import argparse
import gzip
import json
import math
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import build_corrected_tenure_off as core

BASE = Path(__file__).resolve().parent
DB = BASE / "impact_database"
ROSTER = DB / "roster_tenure"
CORE_CHECKPOINTS = DB / "core_checkpoints"
GAMES = ROSTER / "regular_season_games.jsonl.gz"
WORKSET_SUMMARY = core.OUT / "corrected_off_workset_summary.json"


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def load_gzip_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_games() -> dict[tuple[str, int], int]:
    if not GAMES.exists():
        raise RuntimeError(f"missing regular-season schedule: {GAMES}")
    counts: dict[tuple[str, int], int] = defaultdict(int)
    with gzip.open(GAMES, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            game = json.loads(line)
            season = str(game["season"])
            counts[(season, int(game["home_team_id"]))] += 1
            counts[(season, int(game["away_team_id"]))] += 1
    return dict(counts)


def canonical_player_name(checkpoint: dict[str, Any], player_id: str, fallback: str) -> str:
    matches = []
    for row in checkpoint.get("player_totals") or []:
        rid = core.clean_id(row.get("EntityId") or row.get("RowId") or row.get("PlayerId"))
        if rid == player_id:
            name = str(row.get("Name") or row.get("ShortName") or "").strip()
            if name:
                matches.append(name)
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    return fallback


def core_result_for_window(window: dict[str, Any]) -> dict[str, Any] | None:
    season = str(window.get("season") or "")
    team_id = int(window.get("team_id") or 0)
    player_id = core.clean_id(window.get("player_id"))
    fallback_name = str(window.get("player") or window.get("player_name") or "").strip()
    if not season or not team_id or not player_id:
        return None

    checkpoint_path = CORE_CHECKPOINTS / season / f"{team_id}.json.gz"
    checkpoint = load_gzip_json(checkpoint_path)
    if checkpoint.get("complete") is not True or checkpoint.get("absent_team_season") is True:
        return None

    player_name = canonical_player_name(checkpoint, player_id, fallback_name)
    target = normalize_name(player_name)
    if not target:
        return None

    metric_rows: list[dict[str, Any]] = []
    minute_pairs: set[tuple[float, float]] = set()
    results = checkpoint.get("team_on_off_results")
    if not isinstance(results, dict) or not results:
        return None

    for metric, rows in results.items():
        if not isinstance(rows, list):
            continue
        matches = [r for r in rows if isinstance(r, dict) and normalize_name(r.get("Name")) == target]
        if len(matches) != 1:
            continue
        row = matches[0]
        minutes_on = finite(row.get("MinutesOn"))
        minutes_off = finite(row.get("MinutesOff"))
        if minutes_on is not None and minutes_off is not None:
            minute_pairs.add((minutes_on, minutes_off))
        metric_rows.append({
            "metric": str(metric),
            "on": finite(row.get("On")),
            "off_corrected": finite(row.get("Off")),
            "on_minus_off_corrected": finite(row.get("On-Off")),
            "source_extra": {
                k: v for k, v in row.items()
                if k not in {"Name", "MinutesOn", "MinutesOff", "On", "Off", "On-Off"}
            },
        })

    if not metric_rows or len(minute_pairs) != 1:
        return None
    minutes_on, minutes_off = next(iter(minute_pairs))
    start, end = core.query_dates(window)
    return {
        "complete": True,
        "season": season,
        "team_id": team_id,
        "team_abbr": window.get("team_abbr"),
        "player_id": player_id,
        "player": player_name,
        "tenure_start": window.get("tenure_start"),
        "tenure_end": window.get("tenure_end"),
        "query_start_date": start,
        "query_end_date": end,
        "transaction_day_policy": window.get("transaction_day_policy"),
        "team_games_in_window": window.get("team_games_in_window"),
        "tenure_source": window.get("source") or window.get("sources"),
        "tenure_confidence": window.get("confidence") or window.get("tenure_confidence"),
        "boundary_resolution": window.get("same_day_resolution"),
        "minutes_on": minutes_on,
        "minutes_off": minutes_off,
        "metric_count": len(metric_rows),
        "metrics": metric_rows,
        "minute_source_row": None,
        "requests": {
            "core_reuse": {
                "ok": True,
                "checkpoint": str(checkpoint_path),
                "reason": "effective tenure covers the team's complete regular-season schedule",
            }
        },
        "collection_source": "completed_core_reuse",
        "generated_utc": core.now(),
    }


def write_cache(window: dict[str, Any], result: dict[str, Any]) -> Path:
    start, end = core.query_dates(window)
    path = core.cache_path(window, start, end)
    core.CACHE.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = load_gzip_json(path)
        if existing.get("complete") is True:
            return path
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    tmp.replace(path)
    return path


def build() -> dict[str, Any]:
    review = core.load_json(core.REVIEW)
    if review.get("stage1_exact_ready") is not True or int(review.get("review_queue_windows") or 0) != 0:
        raise RuntimeError(f"Stage 1 not exact-ready: {review}")

    windows = core.load_rows(core.WINDOWS)
    unresolved = [w for w in windows if w.get("schedule_boundary_status") != "resolved"]
    if unresolved:
        raise RuntimeError(f"Stage 1 exact-ready gate conflicts with {len(unresolved)} unresolved windows")

    impact = [
        w for w in windows
        if core.clean_id(w.get("player_id"))
        and not bool(w.get("zero_minute_only"))
        and not bool(w.get("zero_game_window"))
    ]
    team_game_counts = load_games()
    full_schedule: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for window in impact:
        key = (str(window.get("season") or ""), int(window.get("team_id") or 0))
        total_games = team_game_counts.get(key)
        in_window = window.get("team_games_in_window")
        if total_games is not None and in_window is not None and int(in_window) == int(total_games):
            full_schedule.append(window)
        else:
            partial.append(window)

    reused = 0
    reuse_misses: list[dict[str, Any]] = []
    for window in full_schedule:
        result = core_result_for_window(window)
        if result is None:
            reuse_misses.append({
                "season": window.get("season"),
                "team_id": window.get("team_id"),
                "team_abbr": window.get("team_abbr"),
                "player_id": window.get("player_id"),
                "player_name": window.get("player_name") or window.get("player"),
            })
            continue
        write_cache(window, result)
        reused += 1

    summary = {
        "generated_utc": core.now(),
        "stage1_exact_ready": True,
        "impact_windows_total": len(impact),
        "full_team_schedule_windows": len(full_schedule),
        "core_reused_windows": reused,
        "full_schedule_core_reuse_misses": len(reuse_misses),
        "partial_tenure_windows_requiring_collection": len(partial),
        "network_collection_upper_bound": len(partial) + len(reuse_misses),
        "reuse_fraction": round(reused / len(impact), 6) if impact else 0.0,
        "reuse_miss_examples": reuse_misses[:100],
        "policy": (
            "Reuse completed 780/780 core ON/OFF for any effective player-team tenure that covers "
            "the team's complete regular-season schedule; query only partial-tenure or unmatched windows."
        ),
        "core_database_rerun": False,
        "teammate_pair_analysis": False,
    }
    core.OUT.mkdir(parents=True, exist_ok=True)
    WORKSET_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def self_test() -> None:
    assert normalize_name("Nikola Jokić") == normalize_name("Nikola Jokic")
    assert finite("12.5") == 12.5
    assert finite(None) is None
    print("prepare_corrected_off_workset self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        build()


if __name__ == "__main__":
    main()
