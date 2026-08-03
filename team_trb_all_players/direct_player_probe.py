from __future__ import annotations

import json
import math
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API = "https://api.pbpstats.com/get-totals/nba"
SEASON = os.getenv("PBPSTATS_PROBE_SEASON", "2025-26")
TEAM_ID = os.getenv("PBPSTATS_PROBE_TEAM_ID", "1610612745")  # Houston Rockets
PLAYER_ID = os.getenv("PBPSTATS_PROBE_PLAYER_ID", "203500")  # Steven Adams
PLAYER_NAME = os.getenv("PBPSTATS_PROBE_PLAYER_NAME", "Steven Adams")
OUT = Path(os.getenv("PBPSTATS_DIRECT_PROBE_OUT", "team_trb_all_players/direct_player_probe_output"))

# Exact values already validated from adaptive lineup extraction.
EXPECTED_SECONDS = float(os.getenv("PBPSTATS_EXPECTED_SECONDS", "43821.9"))
EXPECTED_TEAM_REBOUNDS = float(os.getenv("PBPSTATS_EXPECTED_TEAM_REBOUNDS", "905"))
EXPECTED_OPP_REBOUNDS = float(os.getenv("PBPSTATS_EXPECTED_OPP_REBOUNDS", "620"))

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
OUT.mkdir(parents=True, exist_ok=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite_number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def table_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected payload type: {type(payload).__name__}")
    for key in ("multi_row_table_data", "single_row_table_data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [value]
    raise ValueError(f"No table rows in payload; keys={sorted(payload)}")


def fetch(entity_type: str, team_id: str | None) -> dict[str, Any]:
    params = {
        "Season": SEASON,
        "SeasonType": "Regular Season",
        "Type": entity_type,
    }
    if team_id:
        params["TeamId"] = team_id

    errors: list[str] = []
    for attempt in range(1, 7):
        started = time.monotonic()
        try:
            response = SESSION.get(API, params=params, timeout=(10, 60))
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]!r}")
            response.raise_for_status()
            payload = response.json()
            rows = table_rows(payload)
            return {
                "ok": True,
                "entity_type": entity_type,
                "team_id": team_id,
                "url": response.url,
                "status_code": response.status_code,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "row_count": len(rows),
                "payload_keys": sorted(payload),
                "rows": rows,
            }
        except Exception as exc:  # preserve every failure for diagnosis
            errors.append(f"attempt {attempt}: {exc!r}")
            if attempt < 6:
                time.sleep(min(2 ** (attempt - 1), 12) + random.random())
    return {
        "ok": False,
        "entity_type": entity_type,
        "team_id": team_id,
        "errors": errors,
        "rows": [],
    }


def find_player_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exact_id = [row for row in rows if str(row.get("EntityId")) == PLAYER_ID]
    if exact_id:
        return exact_id
    lowered = PLAYER_NAME.casefold()
    return [row for row in rows if lowered in str(row.get("Name") or "").casefold()]


def numeric_fields(row: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for key, value in row.items():
        number = finite_number(value)
        if number is not None:
            values[key] = number
    return values


def rebound_candidates(row: dict[str, Any], expected: float) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key, value in numeric_fields(row).items():
        normalized = key.casefold()
        if "rebound" not in normalized and "reb" not in normalized:
            continue
        if any(token in normalized for token in ("pct", "percent", "rate", "chance")):
            continue
        candidates.append({
            "field": key,
            "value": value,
            "difference_from_expected": value - expected,
            "exact_match": abs(value - expected) <= 1e-6,
        })
    return sorted(candidates, key=lambda item: (abs(item["difference_from_expected"]), item["field"]))


def seconds_candidates(row: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key, value in numeric_fields(row).items():
        normalized = key.casefold()
        converted = value
        if "second" in normalized:
            pass
        elif "minute" in normalized:
            converted = value * 60.0
        else:
            continue
        candidates.append({
            "field": key,
            "raw_value": value,
            "seconds_value": converted,
            "difference_from_expected": converted - EXPECTED_SECONDS,
            "exact_match": abs(converted - EXPECTED_SECONDS) <= 1e-3,
        })
    return sorted(candidates, key=lambda item: (abs(item["difference_from_expected"]), item["field"]))


def inspect_pair(player_result: dict[str, Any], opponent_result: dict[str, Any], label: str) -> dict[str, Any]:
    player_rows = find_player_rows(player_result.get("rows", []))
    opponent_rows = find_player_rows(opponent_result.get("rows", []))
    report: dict[str, Any] = {
        "label": label,
        "player_rows_found": len(player_rows),
        "opponent_rows_found": len(opponent_rows),
        "validated": False,
    }
    if len(player_rows) != 1 or len(opponent_rows) != 1:
        report["reason"] = "Expected exactly one Adams row in each response"
        report["player_matches"] = player_rows
        report["opponent_matches"] = opponent_rows
        return report

    player_row = player_rows[0]
    opponent_row = opponent_rows[0]
    team_rebounds = rebound_candidates(player_row, EXPECTED_TEAM_REBOUNDS)
    opp_rebounds = rebound_candidates(opponent_row, EXPECTED_OPP_REBOUNDS)
    seconds = seconds_candidates(player_row)
    report.update({
        "player_row": player_row,
        "opponent_row": opponent_row,
        "player_keys": sorted(player_row),
        "opponent_keys": sorted(opponent_row),
        "seconds_candidates": seconds,
        "team_rebound_candidates": team_rebounds,
        "opponent_rebound_candidates": opp_rebounds,
    })

    seconds_match = next((item for item in seconds if item["exact_match"]), None)
    team_match = next((item for item in team_rebounds if item["exact_match"]), None)
    opp_match = next((item for item in opp_rebounds if item["exact_match"]), None)
    report["validated"] = bool(seconds_match and team_match and opp_match)
    if report["validated"]:
        report["selected_schema"] = {
            "seconds_field": seconds_match["field"],
            "team_rebounds_field": team_match["field"],
            "opponent_rebounds_field": opp_match["field"],
        }
        report["calculated_team_trb_pct"] = (
            EXPECTED_TEAM_REBOUNDS
            / (EXPECTED_TEAM_REBOUNDS + EXPECTED_OPP_REBOUNDS)
            * 100.0
        )
    else:
        report["reason"] = "Direct rows did not reproduce all three exact validated values"
    return report


def main() -> int:
    started = now()
    requests_to_make = [
        ("global_player", "Player", None),
        ("global_player_opponent", "PlayerOpponent", None),
        ("team_player", "Player", TEAM_ID),
        ("team_player_opponent", "PlayerOpponent", TEAM_ID),
    ]
    results: dict[str, Any] = {}
    for name, entity_type, team_id in requests_to_make:
        print(f"Fetching {name}: Type={entity_type} TeamId={team_id or 'ALL'}", flush=True)
        result = fetch(entity_type, team_id)
        results[name] = result
        print(
            f"  ok={result.get('ok')} rows={result.get('row_count', 0)} "
            f"elapsed={result.get('elapsed_seconds', '')}",
            flush=True,
        )

    global_report = inspect_pair(
        results["global_player"], results["global_player_opponent"], "global"
    )
    team_report = inspect_pair(
        results["team_player"], results["team_player_opponent"], "team-specific"
    )
    validated_report = global_report if global_report["validated"] else team_report if team_report["validated"] else None

    output = {
        "started_utc": started,
        "completed_utc": now(),
        "season": SEASON,
        "team_id": TEAM_ID,
        "player_id": PLAYER_ID,
        "player_name": PLAYER_NAME,
        "expected": {
            "seconds_played": EXPECTED_SECONDS,
            "team_rebounds": EXPECTED_TEAM_REBOUNDS,
            "opponent_rebounds": EXPECTED_OPP_REBOUNDS,
            "team_trb_pct": EXPECTED_TEAM_REBOUNDS
            / (EXPECTED_TEAM_REBOUNDS + EXPECTED_OPP_REBOUNDS)
            * 100.0,
        },
        "requests": results,
        "global_report": global_report,
        "team_specific_report": team_report,
        "direct_route_validated": validated_report is not None,
        "preferred_scope": validated_report["label"] if validated_report else None,
        "selected_schema": validated_report.get("selected_schema") if validated_report else None,
    }

    output_path = OUT / "probe_result.json"
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    schema_path = OUT / "validated_schema.json"
    if validated_report:
        schema_path.write_text(
            json.dumps(
                {
                    "season": SEASON,
                    "scope": validated_report["label"],
                    **validated_report["selected_schema"],
                    "validated_against": output["expected"],
                    "validated_utc": now(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(
            "VALIDATED direct player route: "
            f"scope={validated_report['label']} schema={validated_report['selected_schema']}",
            flush=True,
        )
        print(f"Evidence: {output_path}", flush=True)
        return 0

    print("NOT VALIDATED: direct player rows do not reproduce the known Adams totals.", flush=True)
    print(f"Schema evidence saved to {output_path}", flush=True)
    # Exit zero deliberately so the evidence can always be committed and inspected.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
