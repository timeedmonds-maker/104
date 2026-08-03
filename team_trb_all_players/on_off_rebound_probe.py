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

BASE = "https://api.pbpstats.com/get-on-off/nba"
SEASON = os.getenv("PBPSTATS_PROBE_SEASON", "2025-26")
TEAM_ID = os.getenv("PBPSTATS_PROBE_TEAM_ID", "1610612745")
PLAYER_ID = os.getenv("PBPSTATS_PROBE_PLAYER_ID", "203500")
PLAYER_NAME = os.getenv("PBPSTATS_PROBE_PLAYER_NAME", "Steven Adams")
OUT = Path(os.getenv("PBPSTATS_ON_OFF_PROBE_OUT", "team_trb_all_players/on_off_rebound_probe_output"))
EXPECTED = {
    "seconds": 43821.9,
    "team_rebounds": 905.0,
    "opponent_rebounds": 620.0,
    "team_trb_pct": 905.0 / (905.0 + 620.0) * 100.0,
}
STATS = [
    "Rebounds", "ReboundsOpponent", "OpponentRebounds", "TeamRebounds",
    "TotalReboundPct", "ReboundPct", "OffRebounds", "DefRebounds",
    "OffReboundsOpponent", "DefReboundsOpponent", "OffReboundPct", "DefReboundPct",
]
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


def number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def fetch(label: str, path: str, params: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, 5):
        started = time.monotonic()
        try:
            response = SESSION.get(f"{BASE}/{path}", params=params, timeout=(10, 60))
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]!r}")
            response.raise_for_status()
            payload = response.json()
            return {
                "label": label, "ok": True, "url": response.url,
                "status_code": response.status_code,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "payload": payload,
            }
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc!r}")
            if attempt < 4:
                time.sleep(min(2 ** (attempt - 1), 8) + random.random())
    return {"label": label, "ok": False, "errors": errors, "payload": None}


def walk(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def compact_matches(payload: Any) -> dict[str, Any]:
    player_rows: list[dict[str, Any]] = []
    exact_values: list[dict[str, Any]] = []
    rebound_fields: list[dict[str, Any]] = []
    targets = list(EXPECTED.values())
    for path, row in walk(payload):
        row_text = json.dumps(row, sort_keys=True, default=str).casefold()
        if PLAYER_ID in row_text or PLAYER_NAME.casefold() in row_text:
            player_rows.append({"path": path, "row": row})
        for key, raw in row.items():
            value = number(raw)
            if value is None:
                continue
            key_text = str(key).casefold()
            if "reb" in key_text:
                rebound_fields.append({"path": f"{path}.{key}", "value": value})
            for target_name, target in EXPECTED.items():
                tolerance = 1e-3 if target_name == "seconds" else 1e-6
                if abs(value - target) <= tolerance:
                    exact_values.append({
                        "path": f"{path}.{key}", "value": value,
                        "matches": target_name,
                    })
    return {
        "player_rows": player_rows[:20],
        "player_row_count": len(player_rows),
        "exact_expected_matches": exact_values,
        "rebound_fields": rebound_fields[:200],
        "rebound_field_count": len(rebound_fields),
    }


def main() -> int:
    common_team = {"Season": SEASON, "SeasonType": "Regular Season", "TeamId": TEAM_ID}
    requests_to_make: list[tuple[str, str, dict[str, str]]] = [
        ("team_all", "team", dict(common_team)),
        ("team_with_player", "team", {**common_team, "PlayerId": PLAYER_ID}),
        ("player_all", "player", {
            "Season": SEASON, "SeasonType": "Regular Season",
            "PlayerId": PLAYER_ID, "TeamId": TEAM_ID,
        }),
    ]
    for stat in STATS:
        requests_to_make.append((f"stat_{stat}", "stat", {**common_team, "Stat": stat}))

    raw: dict[str, Any] = {}
    summary: dict[str, Any] = {
        "started_utc": now(), "season": SEASON, "team_id": TEAM_ID,
        "player_id": PLAYER_ID, "expected": EXPECTED, "requests": {},
        "route_validated": False, "validated_request": None,
    }
    for label, path, params in requests_to_make:
        print(f"Fetching {label}", flush=True)
        result = fetch(label, path, params)
        raw[label] = result
        matches = compact_matches(result.get("payload")) if result.get("ok") else {
            "player_rows": [], "player_row_count": 0,
            "exact_expected_matches": [], "rebound_fields": [], "rebound_field_count": 0,
        }
        summary["requests"][label] = {
            "ok": result.get("ok", False),
            "status_code": result.get("status_code"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "url": result.get("url"),
            "errors": result.get("errors", []),
            **matches,
        }
        matched = {item["matches"] for item in matches["exact_expected_matches"]}
        if {"seconds", "team_rebounds", "opponent_rebounds"}.issubset(matched):
            summary["route_validated"] = True
            summary["validated_request"] = label
        time.sleep(1.0)

    summary["completed_utc"] = now()
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "raw.json").write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    print(f"route_validated={summary['route_validated']} request={summary['validated_request']}")
    print(f"Evidence saved under {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
