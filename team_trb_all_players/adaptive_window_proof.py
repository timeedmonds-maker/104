from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import random
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

API = "https://api.pbpstats.com/get-totals/nba"
SEASON = os.getenv("PBPSTATS_SEASON", "2025-26")
START = date.fromisoformat(os.getenv("PBPSTATS_FROM_DATE", "2025-10-01"))
END = date.fromisoformat(os.getenv("PBPSTATS_TO_DATE", "2026-04-30"))
TEAM_IDS = [int(x) for x in os.getenv(
    "PBPSTATS_TEAM_IDS", "1610612737,1610612738,1610612743,1610612745"
).split(",") if x]
CONTROLS = {int(x) for x in os.getenv(
    "PBPSTATS_CONTROL_TEAM_IDS", "1610612743,1610612745"
).split(",") if x}
OUT = Path(os.getenv("PBPSTATS_OUTPUT_DIR", "team_trb_all_players/adaptive_proof_output"))
MAX_ROWS = 500
TOP_DAYS = int(os.getenv("PBPSTATS_TOP_WINDOW_DAYS", "31"))
ATTEMPTS = int(os.getenv("PBPSTATS_MAX_ATTEMPTS", "3"))
CONNECT_TIMEOUT = float(os.getenv("PBPSTATS_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("PBPSTATS_READ_TIMEOUT", "45"))
PAUSE = float(os.getenv("PBPSTATS_REQUEST_PAUSE", "0.8"))
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}
FIELDS = (
    "SecondsPlayed", "Minutes", "GamesPlayed", "Points", "OpponentPoints",
    "OffPoss", "DefPoss", "TotalPoss", "PlusMinus", "Rebounds",
    "OffRebounds", "DefRebounds", "FieldGoalsMade", "FieldGoalsAttempted",
    "TwoPtFGM", "TwoPtFGA", "ThreePtFGM", "ThreePtFGA", "FreeThrowsMade",
    "FreeThrowsAttempted", "Turnovers", "Assists", "Steals", "Blocks",
    "Fouls", "SecondChancePoints",
)

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "raw_windows").mkdir(exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
AUDIT: list[dict[str, Any]] = []
MANIFEST: list[dict[str, Any]] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def num(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Non-finite number {value!r}")
    return result


def table_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected payload type {type(payload).__name__}")
    for key in ("multi_row_table_data", "single_row_table_data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [value]
    raise ValueError(f"No table rows; keys={sorted(payload)}")


def fetch(team_id: int, entity_type: str, start: date, end: date, purpose: str) -> list[dict[str, Any]]:
    params = {
        "Season": SEASON,
        "SeasonType": "Regular Season",
        "Type": entity_type,
        "TeamId": str(team_id),
        "FromDate": start.isoformat(),
        "ToDate": end.isoformat(),
    }
    errors: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        begun = time.monotonic()
        status: int | str = ""
        size: int | str = ""
        rows_count: int | str = ""
        try:
            response = SESSION.get(API, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            status = response.status_code
            size = len(response.content)
            if status in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {status}: {response.text[:160]!r}")
            response.raise_for_status()
            rows = table_rows(response.json())
            rows_count = len(rows)
            AUDIT.append({
                "timestamp_utc": now(), "purpose": purpose, "season": SEASON,
                "team_id": team_id, "entity_type": entity_type,
                "from_date": start, "to_date": end, "attempt": attempt,
                "status_code": status, "elapsed_seconds": round(time.monotonic() - begun, 3),
                "response_bytes": size, "rows": rows_count, "error": "",
            })
            time.sleep(PAUSE + random.random() * 0.25)
            return rows
        except Exception as exc:
            error = repr(exc)
            errors.append(f"attempt {attempt}: {error}")
            AUDIT.append({
                "timestamp_utc": now(), "purpose": purpose, "season": SEASON,
                "team_id": team_id, "entity_type": entity_type,
                "from_date": start, "to_date": end, "attempt": attempt,
                "status_code": status, "elapsed_seconds": round(time.monotonic() - begun, 3),
                "response_bytes": size, "rows": rows_count, "error": error,
            })
            if attempt < ATTEMPTS:
                time.sleep(min(2 ** (attempt - 1), 8) + random.random())
    raise RuntimeError("; ".join(errors))


def validate_pair(lineups: list[dict[str, Any]], opponents: list[dict[str, Any]]) -> None:
    if len(lineups) >= MAX_ROWS or len(opponents) >= MAX_ROWS:
        raise ValueError(f"ROW_CAP lineups={len(lineups)} opponents={len(opponents)}")
    if not lineups and not opponents:
        return
    if not lineups or not opponents:
        raise ValueError(f"One-sided response {len(lineups)} vs {len(opponents)}")
    left = {str(row.get("EntityId")): row for row in lineups}
    right = {str(row.get("EntityId")): row for row in opponents}
    if len(left) != len(lineups) or len(right) != len(opponents):
        raise ValueError("Duplicate EntityId")
    if left.keys() != right.keys():
        raise ValueError("Lineup and LineupOpponent EntityIds differ")
    for lineup_id, row in left.items():
        opponent = right[lineup_id]
        if len(lineup_id.split("-")) != 5:
            raise ValueError(f"Not a five-player lineup: {lineup_id}")
        if len([x.strip() for x in str(row.get("Name") or "").split(",")]) != 5:
            raise ValueError(f"Cannot parse five names: {lineup_id}")
        seconds = num(row.get("SecondsPlayed"), num(row.get("Minutes")) * 60)
        opp_seconds = num(opponent.get("SecondsPlayed"), num(opponent.get("Minutes")) * 60)
        if abs(seconds - opp_seconds) > 1:
            raise ValueError(f"Seconds mismatch for {lineup_id}: {seconds} vs {opp_seconds}")


def archive(team_id: int, start: date, end: date, lineups: list[dict[str, Any]], opponents: list[dict[str, Any]]) -> tuple[str, str]:
    directory = OUT / "raw_windows" / str(team_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{start}_{end}.json.gz"
    temp = directory / f"{start}_{end}.json.gz.tmp"
    payload = {
        "season": SEASON, "season_type": "Regular Season", "team_id": team_id,
        "from_date": start.isoformat(), "to_date": end.isoformat(),
        "lineup_rows": lineups, "lineup_opponent_rows": opponents,
    }
    with gzip.open(temp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
    temp.replace(path)
    return str(path.relative_to(OUT)), hashlib.sha256(path.read_bytes()).hexdigest()


def adaptive(team_id: int, start: date, end: date, depth: int = 0) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    print(f"FETCH team={team_id} {start}..{end} depth={depth}", flush=True)
    try:
        lineups = fetch(team_id, "Lineup", start, end, "adaptive-window")
        opponents = fetch(team_id, "LineupOpponent", start, end, "adaptive-window")
        validate_pair(lineups, opponents)
        path, digest = archive(team_id, start, end, lineups, opponents)
        MANIFEST.append({
            "season": SEASON, "team_id": team_id, "from_date": start, "to_date": end,
            "days": (end - start).days + 1, "depth": depth, "status": "success",
            "lineup_rows": len(lineups), "opponent_rows": len(opponents),
            "archive_path": path, "archive_sha256": digest, "reason": "",
        })
        print(f"OK team={team_id} {start}..{end} rows={len(lineups)}", flush=True)
        return [(lineups, opponents)]
    except Exception as exc:
        reason = repr(exc)
        if start < end:
            midpoint = start + timedelta(days=(end - start).days // 2)
            MANIFEST.append({
                "season": SEASON, "team_id": team_id, "from_date": start, "to_date": end,
                "days": (end - start).days + 1, "depth": depth, "status": "split",
                "lineup_rows": "", "opponent_rows": "", "archive_path": "",
                "archive_sha256": "", "reason": reason,
            })
            print(f"SPLIT team={team_id} {start}..{end}: {reason}", flush=True)
            return adaptive(team_id, start, midpoint, depth + 1) + adaptive(team_id, midpoint + timedelta(days=1), end, depth + 1)
        MANIFEST.append({
            "season": SEASON, "team_id": team_id, "from_date": start, "to_date": end,
            "days": 1, "depth": depth, "status": "failed", "lineup_rows": "",
            "opponent_rows": "", "archive_path": "", "archive_sha256": "", "reason": reason,
        })
        print(f"FAIL team={team_id} {start}: {reason}", flush=True)
        return []


def initial_windows() -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = START
    while cursor <= END:
        window_end = min(END, cursor + timedelta(days=TOP_DAYS - 1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def grouped(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        entity_id = str(row.get("EntityId"))
        target = result.setdefault(entity_id, {
            "EntityId": entity_id, "Name": row.get("Name") or "",
            "TeamAbbreviation": row.get("TeamAbbreviation") or "",
        })
        for field in FIELDS:
            target[field] = num(target.get(field)) + num(row.get(field))
    return result


def totals(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    result = {field: 0.0 for field in FIELDS}
    for row in rows:
        for field in FIELDS:
            result[field] += num(row.get(field))
    return result


def differences(expected: dict[str, float], actual: dict[str, float], fields: Iterable[str]) -> list[str]:
    errors: list[str] = []
    for field in fields:
        a, b = expected[field], actual[field]
        if abs(a - b) > max(1e-6, abs(a) * 1e-9, abs(b) * 1e-9):
            errors.append(f"{field}: expected={a}, actual={b}")
    return errors


def aggregate(team_id: int, pairs: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    all_lineups = [row for lineups, _ in pairs for row in lineups]
    all_opponents = [row for _, opponents in pairs for row in opponents]
    lineups = grouped(all_lineups)
    opponents = grouped(all_opponents)
    errors: list[str] = []
    if lineups.keys() != opponents.keys():
        errors.append("Season lineup/opponent EntityIds differ")
    players: dict[str, dict[str, Any]] = {}
    lineup_seconds = 0.0
    for lineup_id, row in lineups.items():
        opponent = opponents.get(lineup_id)
        if opponent is None:
            continue
        ids = lineup_id.split("-")
        names = [x.strip() for x in str(row.get("Name") or "").split(",")]
        if len(ids) != 5 or len(names) != 5:
            errors.append(f"Invalid aggregated lineup {lineup_id}")
            continue
        seconds = num(row.get("SecondsPlayed"), num(row.get("Minutes")) * 60)
        lineup_seconds += seconds
        for player_id, name in zip(ids, names, strict=True):
            item = players.setdefault(player_id, {
                "names": Counter(), "seconds": 0.0, "trb": 0.0, "opp_trb": 0.0,
                "points": 0.0, "opp_points": 0.0, "off_poss": 0.0, "opp_off_poss": 0.0,
            })
            item["names"][name] += seconds
            item["seconds"] += seconds
            item["trb"] += num(row.get("Rebounds"))
            item["opp_trb"] += num(opponent.get("Rebounds"))
            item["points"] += num(row.get("Points"))
            item["opp_points"] += num(opponent.get("Points"))
            item["off_poss"] += num(row.get("OffPoss"))
            item["opp_off_poss"] += num(opponent.get("OffPoss"))
    player_rows: list[dict[str, Any]] = []
    for player_id, item in players.items():
        rebound_events = item["trb"] + item["opp_trb"]
        player_rows.append({
            "player_id": player_id, "player": item["names"].most_common(1)[0][0],
            "season": SEASON, "team_id": team_id, "seconds": item["seconds"],
            "minutes": item["seconds"] / 60, "team_rebounds": item["trb"],
            "opponent_rebounds": item["opp_trb"],
            "team_trb_pct": 100 * item["trb"] / rebound_events if rebound_events else "",
            "team_points": item["points"], "opponent_points": item["opp_points"],
            "team_off_poss": item["off_poss"], "opponent_off_poss": item["opp_off_poss"],
            "ortg": 100 * item["points"] / item["off_poss"] if item["off_poss"] else "",
            "drtg": 100 * item["opp_points"] / item["opp_off_poss"] if item["opp_off_poss"] else "",
        })
    if abs(sum(row["seconds"] for row in player_rows) - 5 * lineup_seconds) > max(1, lineup_seconds * 1e-9):
        errors.append("Five-player seconds check failed")
    summary = {
        "team_id": team_id, "leaf_windows": len(pairs), "unique_lineups": len(lineups),
        "lineup_rows_across_windows": len(all_lineups),
        "max_leaf_rows": max((len(x[0]) for x in pairs), default=0),
        "lineup_totals": totals(lineups.values()), "opponent_totals": totals(opponents.values()),
        "player_rows": len(player_rows),
    }
    return player_rows, summary, errors


def control(team_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "errors": []}
    try:
        lineups = fetch(team_id, "Lineup", START, END, "full-season-control")
        opponents = fetch(team_id, "LineupOpponent", START, END, "full-season-control")
        validate_pair(lineups, opponents)
        result.update({
            "available": True, "lineup_rows": len(lineups), "opponent_rows": len(opponents),
            "lineup_totals": totals(lineups), "opponent_totals": totals(opponents),
        })
    except Exception as exc:
        result["errors"] = [repr(exc)]
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def flush() -> None:
    write_csv(OUT / "request_audit.csv", AUDIT, [
        "timestamp_utc", "purpose", "season", "team_id", "entity_type",
        "from_date", "to_date", "attempt", "status_code", "elapsed_seconds",
        "response_bytes", "rows", "error",
    ])
    write_csv(OUT / "window_manifest.csv", MANIFEST, [
        "season", "team_id", "from_date", "to_date", "days", "depth", "status",
        "lineup_rows", "opponent_rows", "archive_path", "archive_sha256", "reason",
    ])


def main() -> None:
    print(f"Adaptive proof season={SEASON} dates={START}..{END} teams={TEAM_IDS}", flush=True)
    player_rows: list[dict[str, Any]] = []
    team_results: list[dict[str, Any]] = []
    proof_errors: list[str] = []
    try:
        for team_id in TEAM_IDS:
            print(f"=== TEAM {team_id} ===", flush=True)
            pairs: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
            for start, end in initial_windows():
                pairs.extend(adaptive(team_id, start, end))
                flush()
            rows, summary, errors = aggregate(team_id, pairs)
            player_rows.extend(rows)
            failed_days = [str(row["from_date"]) for row in MANIFEST if row["team_id"] == team_id and row["status"] == "failed"]
            if failed_days:
                errors.append(f"Unresolved days: {failed_days}")
            if not pairs:
                errors.append("No successful windows")
            comparison: dict[str, Any] = {"not_requested": True}
            if team_id in CONTROLS:
                comparison = control(team_id)
                if comparison.get("available"):
                    errors.extend("lineup control " + x for x in differences(comparison["lineup_totals"], summary["lineup_totals"], FIELDS))
                    errors.extend("opponent control " + x for x in differences(comparison["opponent_totals"], summary["opponent_totals"], FIELDS))
            result = {**summary, "failed_days": failed_days, "full_season_control": comparison,
                      "validation_errors": errors, "validated": not errors}
            team_results.append(result)
            proof_errors.extend(f"team {team_id}: {error}" for error in errors)
            print(f"TEAM RESULT {team_id}: validated={not errors} errors={errors}", flush=True)
            flush()
        player_rows.sort(key=lambda row: (row["team_id"], -row["minutes"], row["player_id"]))
        write_csv(OUT / "player_team_proof.csv", player_rows, [
            "player_id", "player", "season", "team_id", "seconds", "minutes",
            "team_rebounds", "opponent_rebounds", "team_trb_pct", "team_points",
            "opponent_points", "team_off_poss", "opponent_off_poss", "ortg", "drtg",
        ])
        metadata = {
            "created_at_utc": now(), "season": SEASON, "from_date": str(START),
            "to_date": str(END), "team_ids": TEAM_IDS, "control_team_ids": sorted(CONTROLS),
            "request_attempts": len(AUDIT),
            "successful_leaf_windows": sum(x["status"] == "success" for x in MANIFEST),
            "split_windows": sum(x["status"] == "split" for x in MANIFEST),
            "failed_leaf_windows": sum(x["status"] == "failed" for x in MANIFEST),
            "team_results": team_results, "proof_errors": proof_errors,
            "validated": not proof_errors,
            "method": "Non-overlapping date-window Lineup/LineupOpponent pulls; recursively split capped or failed windows; archive every raw successful pair; aggregate additive counts before deriving on-court rates.",
        }
        (OUT / "proof_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        if proof_errors:
            raise SystemExit(" | ".join(proof_errors))
        (OUT / "proof_complete.json").write_text(json.dumps({"validated": True, "created_at_utc": now()}, indent=2), encoding="utf-8")
        print(json.dumps(metadata, indent=2), flush=True)
    finally:
        flush()


if __name__ == "__main__":
    main()
