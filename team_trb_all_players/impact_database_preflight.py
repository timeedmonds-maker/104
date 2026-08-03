from __future__ import annotations

import json

import impact_database_build as build

SEASON = "2025-26"
TEAM_ID = 1610612745
ADAMS_ID = "203500"


def fail(message: str, detail: object | None = None) -> None:
    if detail is not None:
        print(json.dumps(detail, indent=2, sort_keys=True, default=str))
    raise SystemExit(f"Preflight failed: {message}")


def main() -> int:
    print("Running core preflight for Houston 2025-26...", flush=True)
    core = build.fetch_core_task(SEASON, TEAM_ID)
    if core.get("complete") is not True:
        fail("core endpoint collection did not complete", core)

    player_rows = [
        row for row in core.get("player_totals", [])
        if isinstance(row, dict)
    ]
    adams_total = next(
        (
            row for row in player_rows
            if str(row.get("EntityId") or row.get("RowId") or "") == ADAMS_ID
        ),
        None,
    )
    if adams_total is None:
        fail("Steven Adams was not present in player totals")

    seconds = build.number(adams_total.get("SecondsPlayed"))
    if seconds is None or abs(seconds - 43821.9) > 1e-3:
        fail("Steven Adams seconds did not reconcile", {"seconds": seconds})

    metrics = core.get("team_on_off_results", {})
    if not isinstance(metrics, dict) or len(metrics) < 25:
        fail("team on-off response did not contain a broad metric set", {
            "metric_count": len(metrics) if isinstance(metrics, dict) else None
        })

    rebound = next(
        (
            row for row in core.get("rebound_derived", [])
            if isinstance(row, dict) and str(row.get("player_id")) == ADAMS_ID
        ),
        None,
    )
    if rebound is None:
        fail("Steven Adams rebound row was not produced")
    if (
        build.integer(rebound.get("team_rebounds")) != 905
        or build.integer(rebound.get("opponent_rebounds_exact")) != 620
        or rebound.get("exact") is not True
    ):
        fail("Steven Adams rebound totals did not reconcile", rebound)

    print(
        f"Core preflight passed: {len(player_rows)} players, "
        f"{len(metrics)} team on-off metrics, Adams 905/620.",
        flush=True,
    )

    print("Running teammate-interaction preflight for Houston 2025-26...", flush=True)
    pair = build.fetch_pair_task(SEASON, TEAM_ID)
    if pair.get("complete") is not True:
        fail("teammate-interaction collection did not complete", {
            "completed": len(pair.get("completed_player_ids", [])),
            "expected": len(pair.get("expected_player_ids", [])),
            "errors": pair.get("errors", []),
        })

    adams_pair = next(
        (
            row for row in pair.get("focal_players", [])
            if isinstance(row, dict) and str(row.get("focal_player_id")) == ADAMS_ID
        ),
        None,
    )
    if adams_pair is None:
        fail("Steven Adams teammate-interaction response was not saved")
    if int(adams_pair.get("metric_count", 0)) < 25:
        fail("Steven Adams teammate response was not broad enough", adams_pair)

    build.git_commit_progress("preflight Houston 2025-26")
    print(
        f"Pair preflight passed: {len(pair.get('focal_players', []))} focal players; "
        f"Adams response contains {adams_pair.get('metric_count')} metrics and "
        f"{adams_pair.get('row_count')} teammate-metric rows.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
