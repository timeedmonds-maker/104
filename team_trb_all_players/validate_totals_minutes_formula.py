from __future__ import annotations

import gzip
import json
import math
import unicodedata
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
CORE_ROOT = BASE / "impact_database" / "core_checkpoints"


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def player_name(row: dict[str, Any]) -> str:
    return str(row.get("Name") or row.get("ShortName") or "").strip()


def load(path: Path) -> dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    checkpoints = 0
    players_with_seconds = 0
    matched = 0
    unmatched = 0
    mismatches = 0
    max_on_abs_diff = 0.0
    max_off_abs_diff = 0.0
    examples: list[dict[str, Any]] = []
    tolerance_minutes = 0.02

    for path in sorted(CORE_ROOT.glob("*/*.json.gz")):
        data = load(path)
        if data.get("complete") is not True or data.get("absent_team_season") is True:
            continue
        player_rows = [r for r in data.get("player_totals", []) if isinstance(r, dict)]
        result_map = data.get("team_on_off_results")
        if not player_rows or not isinstance(result_map, dict) or not result_map:
            continue

        seconds_by_name: dict[str, tuple[str, float]] = {}
        total_player_seconds = 0.0
        duplicate_names: set[str] = set()
        for row in player_rows:
            seconds = finite(row.get("SecondsPlayed"))
            name = player_name(row)
            key = norm(name)
            if seconds is None or seconds < 0 or not key:
                continue
            total_player_seconds += seconds
            players_with_seconds += 1
            if key in seconds_by_name:
                duplicate_names.add(key)
            else:
                seconds_by_name[key] = (name, seconds)
        for key in duplicate_names:
            seconds_by_name.pop(key, None)
        if not seconds_by_name or total_player_seconds <= 0:
            continue

        # Every NBA second has five team players on court. Therefore sum(player
        # seconds)/5 is exact team court time, including overtime.
        total_team_minutes = total_player_seconds / 5.0 / 60.0

        metric_rows = None
        metric_name = None
        for candidate in ("OffRebounds", "DefRebounds", "Pts", "Fg2Pct"):
            rows = result_map.get(candidate)
            if isinstance(rows, list) and rows:
                metric_rows = [r for r in rows if isinstance(r, dict)]
                metric_name = candidate
                break
        if metric_rows is None:
            for candidate, rows in result_map.items():
                if isinstance(rows, list) and rows:
                    metric_rows = [r for r in rows if isinstance(r, dict)]
                    metric_name = str(candidate)
                    break
        if not metric_rows:
            continue

        checkpoints += 1
        seen: set[str] = set()
        for row in metric_rows:
            key = norm(row.get("Name"))
            if not key or key not in seconds_by_name or key in seen:
                continue
            seen.add(key)
            name, seconds = seconds_by_name[key]
            actual_on = finite(row.get("MinutesOn"))
            actual_off = finite(row.get("MinutesOff"))
            if actual_on is None or actual_off is None:
                unmatched += 1
                continue
            expected_on = seconds / 60.0
            expected_off = total_team_minutes - expected_on
            on_diff = abs(actual_on - expected_on)
            off_diff = abs(actual_off - expected_off)
            max_on_abs_diff = max(max_on_abs_diff, on_diff)
            max_off_abs_diff = max(max_off_abs_diff, off_diff)
            if on_diff <= tolerance_minutes and off_diff <= tolerance_minutes:
                matched += 1
            else:
                mismatches += 1
                if len(examples) < 12:
                    examples.append({
                        "checkpoint": str(path.relative_to(CORE_ROOT)),
                        "metric": metric_name,
                        "player": name,
                        "expected_minutes_on": expected_on,
                        "actual_minutes_on": actual_on,
                        "expected_minutes_off": expected_off,
                        "actual_minutes_off": actual_off,
                        "on_abs_diff": on_diff,
                        "off_abs_diff": off_diff,
                    })

        # Count player-total rows that could not be mapped into the selected on/off metric.
        unmatched += max(0, len(seconds_by_name) - len(seen))

    compared = matched + mismatches
    summary = {
        "validation": "derive MinutesOn from SecondsPlayed and MinutesOff from sum(team player SecondsPlayed)/5",
        "network_calls": 0,
        "core_checkpoints_checked": checkpoints,
        "players_with_seconds": players_with_seconds,
        "comparisons": compared,
        "matched_within_tolerance": matched,
        "mismatches": mismatches,
        "unmatched_or_missing_minutes": unmatched,
        "tolerance_minutes": tolerance_minutes,
        "max_on_abs_diff": max_on_abs_diff,
        "max_off_abs_diff": max_off_abs_diff,
        "match_rate_pct": round(100.0 * matched / compared, 6) if compared else 0.0,
        "examples": examples,
    }
    print(json.dumps(summary, indent=2))

    # Require broad historical proof across the completed core, not a small sample.
    return 0 if compared >= 5000 and mismatches == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
