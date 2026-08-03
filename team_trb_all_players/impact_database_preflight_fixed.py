from __future__ import annotations

import json

import impact_database_build_fixed as build

SEASON = "2025-26"
TEAM_ID = 1610612745
ADAMS_ID = "203500"


def fail(message: str, detail: object | None = None) -> None:
    if detail is not None:
        print(json.dumps(detail, indent=2, sort_keys=True, default=str))
    raise SystemExit(f"Preflight failed: {message}")


def main() -> int:
    print("Running corrected core preflight for Houston 2025-26...", flush=True)
    core = build.fetch_core_task(SEASON, TEAM_ID)
    if core.get("complete") is not True:
        fail("core endpoint collection did not complete", {
            "error": core.get("error"),
            "completed_profiles": len(core.get("completed_team_profile_ids", [])),
            "expected_profiles": len(core.get("expected_team_profile_ids", [])),
            "errors": core.get("errors", [])[-5:],
            "requests": core.get("requests", {}),
        })

    player_rows = [row for row in core.get("player_totals", []) if isinstance(row, dict)]
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

    profiles = [row for row in core.get("team_profiles", []) if isinstance(row, dict)]
    adams_profile = next(
        (row for row in profiles if str(row.get("focal_player_id")) == ADAMS_ID),
        None,
    )
    if adams_profile is None:
        fail("Steven Adams team on-off profile was not produced")
    if int(adams_profile.get("metric_count", 0)) < 25:
        fail("Steven Adams team profile was not broad enough", adams_profile)

    metrics = core.get("team_on_off_results", {})
    if not isinstance(metrics, dict) or len(metrics) < 25:
        fail("team on-off checkpoint did not contain a broad metric set", {
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

    build.git_commit_progress("core preflight Houston 2025-26")
    print(
        f"Core preflight passed: {len(player_rows)} players, "
        f"{len(metrics)} team metrics, Adams 905/620. "
        "Teammate-pair collection is disabled.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
