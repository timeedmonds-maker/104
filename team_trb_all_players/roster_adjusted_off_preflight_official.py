from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

import roster_adjusted_off_preflight as core

BASE = Path(__file__).resolve().parent
OUT = BASE / "impact_database" / "roster_adjusted_off_preflight_official.json"

# This preflight is deliberately strict about request duration. A blocked NBA
# endpoint must fail quickly and visibly rather than appearing to hang.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20
ATTEMPTS = 2

# Same-day transactions require game-level roster evidence. The NBA box-score
# endpoint is not consistently reachable from cloud runners, so unresolved
# cases are handled through a small, auditable override table. For this
# preflight, Memphis' 2024-02-01 game belongs to Adams' Memphis tenure because
# the trade was completed after that game. Houston had no same-day game to add.
SAME_DAY_TEAM_OVERRIDE = {
    (core.PLAYER_ID, "2024-02-01", core.MEM): True,
    (core.PLAYER_ID, "2024-02-01", core.HOU): False,
}


def log(message: str) -> None:
    print(message, flush=True)


def request_headers(url: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
    }
    if "pbpstats.com" in url:
        headers.update(
            {
                "Origin": "https://www.pbpstats.com",
                "Referer": "https://www.pbpstats.com/",
            }
        )
    else:
        headers.update(
            {
                "Origin": "https://www.nba.com",
                "Referer": "https://www.nba.com/",
            }
        )
    return headers


def fast_get(
    url: str,
    params: dict[str, str] | None = None,
    *,
    text: bool = False,
) -> Any:
    errors: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        log(f"[request {attempt}/{ATTEMPTS}] {url}")
        started = time.monotonic()
        try:
            response = requests.get(
                url,
                params=params,
                headers=request_headers(url),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            response.raise_for_status()
            log(
                f"[response] {response.status_code} in "
                f"{time.monotonic() - started:.1f}s"
            )
            return response.text if text else response.json()
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc!r}")
            log(f"[failed] {errors[-1]}")
            if attempt < ATTEMPTS:
                time.sleep(1)
    raise RuntimeError(f"request failed {url}: {'; '.join(errors)}")


# All imported helpers resolve their global `get` name in the core module.
# Replacing it here gives every request strict timeouts and visible progress.
core.get = fast_get


def official_trade_date_fast() -> tuple[date, dict[str, Any]]:
    report: dict[str, Any] = {"errors": []}
    for url in core.MOVEMENT_URLS:
        try:
            rows = core.movement_rows(fast_get(url))
            log(f"[movement] {len(rows)} rows from {url}")
            dates = [
                parsed
                for parsed in (
                    core.as_date(core.ci(row, "TRANSACTION_DATE", "Date"))
                    for row in rows
                )
                if parsed
            ]
            adams_rows = []
            candidates = []
            for row in rows:
                player_id = str(core.ci(row, "PLAYER_ID", "PlayerId") or "")
                description = str(
                    core.ci(row, "TRANSACTION_DESCRIPTION", "Description") or ""
                )
                slug = str(core.ci(row, "PLAYER_SLUG", "PlayerSlug") or "")
                if (
                    player_id == core.PLAYER_ID
                    or "steven-adams" in slug.casefold()
                    or core.PLAYER_NAME.casefold() in description.casefold()
                ):
                    adams_rows.append(row)
                    transaction_date = core.as_date(
                        core.ci(row, "TRANSACTION_DATE", "Date")
                    )
                    if (
                        transaction_date
                        and transaction_date.year == 2024
                        and "houston" in description.casefold()
                        and "memphis" in description.casefold()
                    ):
                        candidates.append(transaction_date)
            report.update(
                {
                    "source": url,
                    "row_count": len(rows),
                    "minimum_date": min(dates).isoformat() if dates else None,
                    "maximum_date": max(dates).isoformat() if dates else None,
                    "adams_rows": adams_rows,
                }
            )
            if candidates:
                trade_date = min(candidates)
                log(f"[movement] Adams trade date: {trade_date.isoformat()}")
                return trade_date, report
            report["errors"].append(f"{url}: Adams trade row not found")
        except Exception as exc:
            report["errors"].append(f"{url}: {exc!r}")

    # The fallback exists only to let the calculation preflight continue when
    # the official static feed is temporarily blocked. It is explicitly marked
    # in the report and must not be silently used by the historical build.
    fallback = date(2024, 2, 1)
    report.update(
        {
            "source": "audited_preflight_override",
            "fallback_trade_date": fallback.isoformat(),
        }
    )
    log("[movement] official feed unavailable; using audited preflight date 2024-02-01")
    return fallback, report


def build_windows_fast(games: list[dict[str, Any]], trade: date) -> dict[str, Any]:
    memphis_all = core.team_games(games, core.MEM)
    houston_all = core.team_games(games, core.HOU)
    memphis_rows = [game for game in memphis_all if game["_date"] < trade]
    houston_rows = [game for game in houston_all if game["_date"] > trade]
    resolution: list[dict[str, Any]] = []

    for game in games:
        if game["_date"] != trade:
            continue
        participants = {
            int(game.get("HomeTeamId", 0)),
            int(game.get("AwayTeamId", 0)),
        }
        for team_id, destination in (
            (core.MEM, memphis_rows),
            (core.HOU, houston_rows),
        ):
            if team_id not in participants:
                continue
            key = (core.PLAYER_ID, trade.isoformat(), team_id)
            include = SAME_DAY_TEAM_OVERRIDE.get(key)
            if include is None:
                raise RuntimeError(
                    "same-day game requires roster evidence or an audited override: "
                    f"player={core.PLAYER_ID}, date={trade}, team={team_id}, "
                    f"game={game.get('GameId')}"
                )
            resolution.append(
                {
                    "game_id": str(game.get("GameId", "")),
                    "team_id": team_id,
                    "rostered": include,
                    "source": "audited_same_day_override",
                }
            )
            if include:
                destination.append(game)

    windows = {
        "trade_date": trade.isoformat(),
        "same_day_resolution": resolution,
        "memphis": core.window(memphis_rows),
        "houston": core.window(houston_rows),
        "memphis_full_games": len(memphis_all),
        "houston_full_games": len(houston_all),
    }
    log(
        "[windows] Memphis "
        f"{windows['memphis']['from_date']} to {windows['memphis']['to_date']} "
        f"({windows['memphis']['games']} games)"
    )
    log(
        "[windows] Houston "
        f"{windows['houston']['from_date']} to {windows['houston']['to_date']} "
        f"({windows['houston']['games']} games)"
    )
    return windows


def main() -> int:
    log("[stage 1/4] Resolve official roster-tenure boundary")
    trade, movement = official_trade_date_fast()

    log("[stage 2/4] Fetch season games and construct team roster windows")
    windows = build_windows_fast(core.fetch_games(), trade)

    log("[stage 3/4] Fetch roster-window team and opponent totals")
    segments = []
    for name, team_id in (("memphis", core.MEM), ("houston", core.HOU)):
        roster_window = windows[name]
        log(f"[totals] {name} team")
        team = core.fetch_totals(team_id, roster_window, "Team")
        log(f"[totals] {name} opponent")
        opponent = core.fetch_totals(team_id, roster_window, "Opponent")
        segment = {
            "team": name,
            "team_id": team_id,
            "window": roster_window,
            "off_metrics": core.metrics(team, opponent),
        }

        # This identity check is useful but not required for the corrected OFF
        # calculation. It has strict timeouts and degrades cleanly if unavailable.
        try:
            log(f"[wowy check] {name} team")
            wowy_team = core.fetch_wowy_off(team_id, roster_window, "Team")
            log(f"[wowy check] {name} opponent")
            wowy_opponent = core.fetch_wowy_off(team_id, roster_window, "Opponent")
            segment["wowy_zero_minute_identity"] = {
                "team": core.identity(team, wowy_team),
                "opponent": core.identity(opponent, wowy_opponent),
                "metrics": core.metrics(wowy_team, wowy_opponent),
            }
        except Exception as exc:
            segment["wowy_zero_minute_identity"] = {
                "error": repr(exc),
                "fallback": (
                    "For a zero-minute roster tenure, every team possession in the "
                    "roster window is OFF by definition."
                ),
            }
            log(f"[wowy check unavailable] {name}: {exc!r}")
        segments.append(segment)

    log("[stage 4/4] Validate and write report")
    report = {
        "definition": (
            "Roster-window OFF includes every team possession while the player officially "
            "belonged to that team, including injury and DNP games."
        ),
        "season": core.SEASON,
        "player_id": core.PLAYER_ID,
        "player": core.PLAYER_NAME,
        "movement_feed": movement,
        "roster_validation": {
            "trade_boundary_source": movement.get("source"),
            "same_day_game_resolution": windows["same_day_resolution"],
            "historical_build_rule": (
                "Unresolved same-day transaction games must enter a manual-review queue; "
                "they may never be guessed or silently assigned."
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
                "movement_source": movement.get("source"),
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
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
