from __future__ import annotations

import json
import sys
from pathlib import Path

from roster_adjusted_off_preflight import (
    HOU,
    MEM,
    PLAYER_ID,
    PLAYER_NAME,
    SEASON,
    build_windows,
    fetch_games,
    fetch_totals,
    fetch_wowy_off,
    identity,
    metrics,
    official_trade_date,
)

BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "roster_adjusted_off_preflight_official.json"


def main() -> int:
    trade, movement = official_trade_date()
    if trade is None:
        raise RuntimeError("could not find Adams' Memphis-to-Houston trade date")

    source = str(movement.get("source") or "")
    if "stats.nba.com" not in source and "nba.com/stats" not in source:
        raise RuntimeError(
            "official NBA movement feed was unavailable; refusing non-official fallback"
        )

    adams_rows = movement.get("adams_rows", [])
    trade_rows = [
        row
        for row in adams_rows
        if str(row.get("TRANSACTION_DATE", "")).startswith("2024-02-01")
        and "Houston Rockets received center Steven Adams from Memphis Grizzlies"
        in str(row.get("TRANSACTION_DESCRIPTION", ""))
    ]
    if not trade_rows:
        raise RuntimeError("official NBA movement row for the Adams trade was not found")

    windows = build_windows(fetch_games(), trade)
    segments = []
    for name, team_id in (("memphis", MEM), ("houston", HOU)):
        roster_window = windows[name]
        team = fetch_totals(team_id, roster_window, "Team")
        opponent = fetch_totals(team_id, roster_window, "Opponent")
        segment = {
            "team": name,
            "team_id": team_id,
            "window": roster_window,
            "off_metrics": metrics(team, opponent),
        }
        try:
            wowy_team = fetch_wowy_off(team_id, roster_window, "Team")
            wowy_opponent = fetch_wowy_off(team_id, roster_window, "Opponent")
            segment["wowy_zero_minute_identity"] = {
                "team": identity(team, wowy_team),
                "opponent": identity(opponent, wowy_opponent),
                "metrics": metrics(wowy_team, wowy_opponent),
            }
        except Exception as exc:
            segment["wowy_zero_minute_identity"] = {
                "error": repr(exc),
                "fallback": (
                    "For a zero-minute roster tenure, every team possession in the "
                    "official roster window is OFF by definition."
                ),
            }
        segments.append(segment)

    report = {
        "definition": (
            "Roster-window OFF includes every team possession while the player officially "
            "belonged to that team, including injury and DNP games."
        ),
        "season": SEASON,
        "player_id": PLAYER_ID,
        "player": PLAYER_NAME,
        "movement_feed": movement,
        "roster_validation": {
            "primary_source": source,
            "official_trade_rows": trade_rows,
            "same_day_game_roster_evidence": windows["same_day_resolution"],
            "note": (
                "The official NBA movement row establishes Memphis as the source team and "
                "Houston as the destination. Any game on the transaction date is assigned "
                "using the official NBA box-score roster, so injured/DNP status does not "
                "end the old-team window early."
            ),
        },
        "roster_windows": windows,
        "segments": segments,
        "preflight_passed": all(
            segment["off_metrics"]["minutes"] is not None for segment in segments
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "preflight_passed": report["preflight_passed"],
                "movement_source": source,
                "movement_minimum_date": movement.get("minimum_date"),
                "trade_date": windows["trade_date"],
                "same_day_resolution": windows["same_day_resolution"],
                "memphis_window": windows["memphis"],
                "houston_window": windows["houston"],
                "segments": [
                    {
                        "team": segment["team"],
                        **segment["off_metrics"],
                        "wowy_match": segment.get("wowy_zero_minute_identity", {})
                        .get("team", {})
                        .get("match"),
                    }
                    for segment in segments
                ],
                "report": str(OUT),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
