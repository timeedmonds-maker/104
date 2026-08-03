from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

import impact_database_build as base

STAT_URL = "https://api.pbpstats.com/get-on-off/nba/stat"
REBOUND_STATS = ("OffRebounds", "DefRebounds", "OffReboundPct", "DefReboundPct")

# Re-export helpers used by the preflight and by callers that previously
# imported impact_database_build directly.
number = base.number
integer = base.integer
now = base.now
git_commit_progress = base.git_commit_progress
fetch_pair_task = base.fetch_pair_task


def list_results(payload: Any) -> list[dict[str, Any]]:
    """Return an endpoint's list-shaped results payload."""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def positive_players(player_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in player_rows:
        player_id, player_name = base.player_identity(row)
        seconds = base.number(row.get("SecondsPlayed"))
        if player_id and player_name and seconds is not None and seconds > 0:
            output.append(row)
    return output


def rebound_metric_rows(
    season: str,
    team_id: int,
    player_rows: list[dict[str, Any]],
    stat_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Normalize the four team-season rebound-stat responses."""
    name_index = base.build_name_index(player_rows)
    output: list[dict[str, Any]] = []
    for metric in REBOUND_STATS:
        for row in stat_results.get(metric, []):
            subject_name = str(row.get("Name") or "").strip()
            mapped = name_index.get(base.normalize_name(subject_name), {})
            subject_id, canonical_name = base.player_identity(mapped)
            output.append({
                "season": season,
                "team_id": team_id,
                "focal_player_id": "",
                "focal_player": "",
                "subject_player_id": subject_id,
                "subject_player": canonical_name or subject_name,
                "metric": metric,
                "minutes_on": row.get("MinutesOn"),
                "minutes_off": row.get("MinutesOff"),
                "on": row.get("On"),
                "off": row.get("Off"),
                "on_off": row.get("On-Off"),
                "source_extra": {
                    key: value for key, value in row.items()
                    if key not in {"Name", "MinutesOn", "MinutesOff", "On", "Off", "On-Off"}
                },
            })
    return output


def displayed_minutes_by_player(
    player_rows: list[dict[str, Any]],
    stat_results: dict[str, list[dict[str, Any]]],
) -> dict[str, tuple[Any, Any]]:
    """Use the stat endpoint's displayed on/off minutes for broad profiles."""
    name_index = base.build_name_index(player_rows)
    output: dict[str, tuple[Any, Any]] = {}
    source_rows = stat_results.get("OffRebounds", [])
    for row in source_rows:
        mapped = name_index.get(base.normalize_name(str(row.get("Name") or "")), {})
        player_id, _ = base.player_identity(mapped)
        if player_id:
            output[player_id] = (row.get("MinutesOn"), row.get("MinutesOff"))
    return output


def metric_map_from_profiles(
    player_rows: list[dict[str, Any]],
    stat_results: dict[str, list[dict[str, Any]]],
    profiles: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Convert one /team response per focal player into the dict-of-player-rows
    shape consumed by the existing season aggregator.
    """
    minutes = displayed_minutes_by_player(player_rows, stat_results)
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        player_id = str(profile.get("focal_player_id") or "")
        player_name = str(profile.get("focal_player") or "")
        minutes_on, minutes_off = minutes.get(player_id, (None, None))
        for row in profile.get("results", []):
            if not isinstance(row, dict):
                continue
            metric = str(row.get("Stat") or "").strip()
            if not metric:
                continue
            output[metric].append({
                "Name": player_name,
                "MinutesOn": minutes_on,
                "MinutesOff": minutes_off,
                "On": row.get("On"),
                "Off": row.get("Off"),
                "On-Off": row.get("On-Off"),
                **{
                    key: value for key, value in row.items()
                    if key not in {"Stat", "On", "Off", "On-Off"}
                },
            })
    return dict(output)


def partial_core(
    *,
    season: str,
    team_id: int,
    attempts: int,
    started: float,
    player_rows: list[dict[str, Any]],
    totals_meta: dict[str, Any],
    totals_payload_meta: dict[str, Any],
    stat_results: dict[str, list[dict[str, Any]]],
    stat_request_meta: dict[str, Any],
    profiles: list[dict[str, Any]],
    completed_ids: set[str],
    expected_ids: set[str],
    errors: list[dict[str, Any]],
    error: str = "",
) -> dict[str, Any]:
    enriched_players = [{"season": season, "team_id": team_id, **row} for row in player_rows]
    team_results = metric_map_from_profiles(player_rows, stat_results, profiles)
    normalized_rebound_rows = rebound_metric_rows(season, team_id, player_rows, stat_results)
    rebound_rows = base.derive_rebounds(player_rows, normalized_rebound_rows, season, team_id)
    complete = expected_ids.issubset(completed_ids) and all(
        stat in stat_results and bool(stat_results[stat]) for stat in REBOUND_STATS
    )
    result: dict[str, Any] = {
        "complete": complete,
        "absent_team_season": False,
        "season": season,
        "team_id": team_id,
        "attempts": attempts,
        "player_totals": enriched_players,
        "completed_team_profile_ids": sorted(completed_ids),
        "expected_team_profile_ids": sorted(expected_ids),
        "team_profiles": profiles,
        "team_on_off_results": team_results,
        "rebound_stat_results": stat_results,
        "rebound_derived": rebound_rows,
        "errors": errors[-100:],
        "requests": {
            "player_totals": totals_meta,
            "rebound_stats": stat_request_meta,
        },
        "payload_meta": {
            "player_totals": totals_payload_meta,
        },
        "generated_at_utc": base.now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    if error:
        result["error"] = error
    return result


def fetch_core_task(season: str, team_id: int) -> dict[str, Any]:
    """
    Build one team-season core checkpoint.

    The /team endpoint requires PlayerId, so broad team on/off profiles are
    fetched once per player. Four /stat requests are fetched once per
    team-season to preserve exact rebound counts and displayed minutes.
    """
    path = base.core_path(season, team_id)
    existing = base.read_gzip_json(path)
    attempts = base.prior_attempts(path) + 1
    started = time.monotonic()
    common = {"Season": season, "SeasonType": "Regular Season", "TeamId": str(team_id)}

    totals_result = base.request_json(base.TOTALS_URL, {**common, "Type": "Player"})
    if not totals_result.get("ok"):
        if totals_result.get("absent"):
            result = {
                "complete": True,
                "absent_team_season": True,
                "season": season,
                "team_id": team_id,
                "attempts": attempts,
                "player_totals": [],
                "completed_team_profile_ids": [],
                "expected_team_profile_ids": [],
                "team_profiles": [],
                "team_on_off_results": {},
                "rebound_stat_results": {},
                "rebound_derived": [],
                "requests": {"player_totals": base.response_meta(totals_result)},
                "generated_at_utc": base.now(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            base.write_gzip_json(path, result)
            return result
        result = {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "error": "player totals request failed",
            "requests": {"player_totals": base.response_meta(totals_result)},
            "generated_at_utc": base.now(),
        }
        base.write_gzip_json(path, result)
        return result

    player_rows = base.totals_rows(totals_result.get("payload"))
    if not player_rows:
        result = {
            "complete": True,
            "absent_team_season": True,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "player_totals": [],
            "completed_team_profile_ids": [],
            "expected_team_profile_ids": [],
            "team_profiles": [],
            "team_on_off_results": {},
            "rebound_stat_results": {},
            "rebound_derived": [],
            "requests": {"player_totals": base.response_meta(totals_result)},
            "payload_meta": {"player_totals": base.payload_meta(totals_result.get("payload"))},
            "generated_at_utc": base.now(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        base.write_gzip_json(path, result)
        return result

    active_players = positive_players(player_rows)
    expected_ids = {base.player_identity(row)[0] for row in active_players}

    existing_profiles = [
        row for row in existing.get("team_profiles", [])
        if isinstance(row, dict) and str(row.get("focal_player_id") or "") in expected_ids
    ]
    profile_by_id = {
        str(row.get("focal_player_id")): row
        for row in existing_profiles
        if str(row.get("focal_player_id") or "")
    }
    completed_ids = set(profile_by_id)
    errors = [row for row in existing.get("errors", []) if isinstance(row, dict)]

    stat_results: dict[str, list[dict[str, Any]]] = {}
    existing_stats = existing.get("rebound_stat_results", {})
    if isinstance(existing_stats, dict):
        for stat in REBOUND_STATS:
            rows = existing_stats.get(stat)
            if isinstance(rows, list) and rows:
                stat_results[stat] = [row for row in rows if isinstance(row, dict)]

    stat_request_meta: dict[str, Any] = {}
    for stat in REBOUND_STATS:
        if stat in stat_results and stat_results[stat]:
            stat_request_meta[stat] = {"reused_checkpoint": True}
            continue
        fetched = base.request_json(STAT_URL, {**common, "Stat": stat})
        stat_request_meta[stat] = base.response_meta(fetched)
        rows = list_results(fetched.get("payload")) if fetched.get("ok") else []
        if not rows:
            result = partial_core(
                season=season,
                team_id=team_id,
                attempts=attempts,
                started=started,
                player_rows=player_rows,
                totals_meta=base.response_meta(totals_result),
                totals_payload_meta=base.payload_meta(totals_result.get("payload")),
                stat_results=stat_results,
                stat_request_meta=stat_request_meta,
                profiles=list(profile_by_id.values()),
                completed_ids=completed_ids,
                expected_ids=expected_ids,
                errors=errors,
                error=f"{stat} request failed or returned no rows",
            )
            base.write_gzip_json(path, result)
            return result
        stat_results[stat] = rows

    for player in active_players:
        player_id, player_name = base.player_identity(player)
        seconds = base.number(player.get("SecondsPlayed"))
        if player_id in completed_ids:
            continue
        params = {**common, "PlayerId": player_id}
        fetched = base.request_json(base.ON_OFF_TEAM_URL, params)
        rows = list_results(fetched.get("payload")) if fetched.get("ok") else []
        if not rows:
            errors.append({
                "stage": "team_profile",
                "player_id": player_id,
                "player": player_name,
                "request": base.response_meta(fetched),
                "at_utc": base.now(),
            })
            current = partial_core(
                season=season,
                team_id=team_id,
                attempts=attempts,
                started=started,
                player_rows=player_rows,
                totals_meta=base.response_meta(totals_result),
                totals_payload_meta=base.payload_meta(totals_result.get("payload")),
                stat_results=stat_results,
                stat_request_meta=stat_request_meta,
                profiles=list(profile_by_id.values()),
                completed_ids=completed_ids,
                expected_ids=expected_ids,
                errors=errors,
                error="one or more team-profile requests incomplete",
            )
            base.write_gzip_json(path, current)
            continue

        profile_by_id[player_id] = {
            "season": season,
            "team_id": team_id,
            "focal_player_id": player_id,
            "focal_player": player_name,
            "seconds": seconds,
            "request": base.response_meta(fetched),
            "payload_meta": base.payload_meta(fetched.get("payload")),
            "metric_count": len(rows),
            "results": rows,
        }
        completed_ids.add(player_id)
        current = partial_core(
            season=season,
            team_id=team_id,
            attempts=attempts,
            started=started,
            player_rows=player_rows,
            totals_meta=base.response_meta(totals_result),
            totals_payload_meta=base.payload_meta(totals_result.get("payload")),
            stat_results=stat_results,
            stat_request_meta=stat_request_meta,
            profiles=list(profile_by_id.values()),
            completed_ids=completed_ids,
            expected_ids=expected_ids,
            errors=errors,
        )
        base.write_gzip_json(path, current)

    result = partial_core(
        season=season,
        team_id=team_id,
        attempts=attempts,
        started=started,
        player_rows=player_rows,
        totals_meta=base.response_meta(totals_result),
        totals_payload_meta=base.payload_meta(totals_result.get("payload")),
        stat_results=stat_results,
        stat_request_meta=stat_request_meta,
        profiles=list(profile_by_id.values()),
        completed_ids=completed_ids,
        expected_ids=expected_ids,
        errors=errors,
        error="" if expected_ids.issubset(completed_ids) else "team-profile collection incomplete",
    )
    base.write_gzip_json(path, result)
    return result


# Install the corrected core collector into the original orchestrator. The
# original aggregation and teammate-pair layers remain compatible because the
# checkpoint includes the same player_totals, team_on_off_results, and
# rebound_derived keys.
base.fetch_core_task = fetch_core_task


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
