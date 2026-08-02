from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

API = "https://api.pbpstats.com/get-totals/nba"
SEASON = os.environ["PBPSTATS_SEASON"]
START = date.fromisoformat(os.environ["PBPSTATS_FROM_DATE"])
END = date.fromisoformat(os.environ["PBPSTATS_TO_DATE"])
TEAM_IDS = [int(x) for x in os.environ["PBPSTATS_TEAM_IDS"].split(",") if x]
GROUP = os.getenv("PBPSTATS_GROUP", "0")
OUT = Path(os.getenv("PBPSTATS_OUTPUT_DIR", "team_trb_all_players/full_build_output"))
MAX_ROWS = 500
TOP_DAYS = int(os.getenv("PBPSTATS_TOP_WINDOW_DAYS", "14"))
CONNECT_TIMEOUT = float(os.getenv("PBPSTATS_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("PBPSTATS_READ_TIMEOUT", "35"))
PAUSE = float(os.getenv("PBPSTATS_REQUEST_PAUSE", "0.45"))
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}
ADDITIVE_FIELDS = (
    "SecondsPlayed", "Points", "OpponentPoints", "OffPoss", "DefPoss",
    "TotalPoss", "PlusMinus", "Rebounds", "OffRebounds", "DefRebounds",
    "FieldGoalsMade", "FieldGoalsAttempted", "TwoPtFGM", "TwoPtFGA",
    "ThreePtFGM", "ThreePtFGA", "FreeThrowsMade", "FreeThrowsAttempted",
    "Turnovers", "Assists", "Steals", "Blocks", "Fouls",
    "SecondChancePoints",
)
CONTROL_FIELDS = (
    "SecondsPlayed", "Points", "OffPoss", "Rebounds", "OffRebounds",
    "DefRebounds", "FieldGoalsMade", "FieldGoalsAttempted", "Turnovers",
    "Assists", "Steals", "Blocks", "Fouls",
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


def attempts_for(days: int, purpose: str) -> int:
    if purpose == "season-control":
        return 4
    if days >= 8:
        return 1
    if days >= 2:
        return 2
    return 4


def fetch(team_id: int, entity_type: str, start: date, end: date, purpose: str) -> list[dict[str, Any]]:
    params = {
        "Season": SEASON,
        "SeasonType": "Regular Season",
        "Type": entity_type,
        "TeamId": str(team_id),
        "FromDate": start.isoformat(),
        "ToDate": end.isoformat(),
    }
    days = (end - start).days + 1
    max_attempts = attempts_for(days, purpose)
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
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
                "group": GROUP, "team_id": team_id, "entity_type": entity_type,
                "from_date": start, "to_date": end, "attempt": attempt,
                "status_code": status,
                "elapsed_seconds": round(time.monotonic() - begun, 3),
                "response_bytes": size, "rows": rows_count, "error": "",
            })
            time.sleep(PAUSE + random.random() * 0.2)
            return rows
        except Exception as exc:
            error = repr(exc)
            errors.append(f"attempt {attempt}: {error}")
            AUDIT.append({
                "timestamp_utc": now(), "purpose": purpose, "season": SEASON,
                "group": GROUP, "team_id": team_id, "entity_type": entity_type,
                "from_date": start, "to_date": end, "attempt": attempt,
                "status_code": status,
                "elapsed_seconds": round(time.monotonic() - begun, 3),
                "response_bytes": size, "rows": rows_count, "error": error,
            })
            if attempt < max_attempts:
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
        names = [x.strip() for x in str(row.get("Name") or "").split(",")]
        if len(names) != 5:
            raise ValueError(f"Cannot parse five names: {lineup_id}")
        seconds = num(row.get("SecondsPlayed"), num(row.get("Minutes")) * 60)
        opp_seconds = num(opponent.get("SecondsPlayed"), num(opponent.get("Minutes")) * 60)
        if abs(seconds - opp_seconds) > 1e-6:
            raise ValueError(f"Seconds mismatch for {lineup_id}: {seconds} vs {opp_seconds}")


def archive(team_id: int, start: date, end: date, lineups: list[dict[str, Any]], opponents: list[dict[str, Any]]) -> tuple[str, str]:
    directory = OUT / "raw_windows" / str(team_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{start}_{end}.json.gz"
    temp = directory / f"{start}_{end}.json.gz.tmp"
    payload = {
        "season": SEASON, "season_type": "Regular Season", "group": GROUP,
        "team_id": team_id, "from_date": start.isoformat(), "to_date": end.isoformat(),
        "lineup_rows": lineups, "lineup_opponent_rows": opponents,
    }
    with gzip.open(temp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
    temp.replace(path)
    return str(path.relative_to(OUT)), hashlib.sha256(path.read_bytes()).hexdigest()


def adaptive(team_id: int, start: date, end: date, depth: int = 0) -> tuple[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], bool]:
    print(f"FETCH team={team_id} {start}..{end} depth={depth}", flush=True)
    try:
        lineups = fetch(team_id, "Lineup", start, end, "adaptive-window")
        opponents = fetch(team_id, "LineupOpponent", start, end, "adaptive-window")
        validate_pair(lineups, opponents)
        path, digest = archive(team_id, start, end, lineups, opponents)
        MANIFEST.append({
            "season": SEASON, "group": GROUP, "team_id": team_id,
            "from_date": start, "to_date": end, "days": (end - start).days + 1,
            "depth": depth, "status": "success", "lineup_rows": len(lineups),
            "opponent_rows": len(opponents), "archive_path": path,
            "archive_sha256": digest, "reason": "",
        })
        print(f"OK team={team_id} {start}..{end} rows={len(lineups)}", flush=True)
        return [(lineups, opponents)], True
    except Exception as exc:
        reason = repr(exc)
        if start < end:
            midpoint = start + timedelta(days=(end - start).days // 2)
            MANIFEST.append({
                "season": SEASON, "group": GROUP, "team_id": team_id,
                "from_date": start, "to_date": end, "days": (end - start).days + 1,
                "depth": depth, "status": "split", "lineup_rows": "",
                "opponent_rows": "", "archive_path": "", "archive_sha256": "",
                "reason": reason,
            })
            print(f"SPLIT team={team_id} {start}..{end}: {reason}", flush=True)
            left_pairs, left_ok = adaptive(team_id, start, midpoint, depth + 1)
            right_pairs, right_ok = adaptive(team_id, midpoint + timedelta(days=1), end, depth + 1)
            return left_pairs + right_pairs, left_ok and right_ok
        MANIFEST.append({
            "season": SEASON, "group": GROUP, "team_id": team_id,
            "from_date": start, "to_date": end, "days": 1, "depth": depth,
            "status": "failed", "lineup_rows": "", "opponent_rows": "",
            "archive_path": "", "archive_sha256": "", "reason": reason,
        })
        print(f"FAIL team={team_id} {start}: {reason}", flush=True)
        return [], False


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
        for field in ADDITIVE_FIELDS:
            target[field] = num(target.get(field)) + num(row.get(field))
    return result


def totals(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    result = {field: 0.0 for field in ADDITIVE_FIELDS}
    for row in rows:
        for field in ADDITIVE_FIELDS:
            result[field] += num(row.get(field))
    return result


def one_control_row(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise ValueError(f"{label} expected one row, got {len(rows)}")
    return rows[0]


def compare_control(actual: dict[str, float], control: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    for field in CONTROL_FIELDS:
        expected = num(control.get(field))
        observed = actual[field]
        if abs(expected - observed) > 1e-6:
            errors.append(f"{label} {field}: control={expected}, lineups={observed}")
    return errors


def expected_active(team_id: int) -> bool:
    start_year = int(SEASON[:4])
    return not (team_id == 1610612766 and start_year < 2004)


def aggregate_team(team_id: int, pairs: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], windows_ok: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    errors: list[str] = []
    if not windows_ok:
        errors.append("one or more one-day windows failed")
    all_lineups = [row for lineups, _ in pairs for row in lineups]
    all_opponents = [row for _, opponents in pairs for row in opponents]
    lineups = grouped(all_lineups)
    opponents = grouped(all_opponents)
    if lineups.keys() != opponents.keys():
        errors.append("Season lineup/opponent EntityIds differ")
    active = expected_active(team_id)
    if active and not lineups:
        errors.append("active franchise returned no lineup rows")
    if not active and not lineups and not opponents:
        return [], {
            "season": SEASON, "group": GROUP, "team_id": team_id,
            "expected_active": False, "validated": True, "errors": "",
            "leaf_windows": sum(1 for row in MANIFEST if row["team_id"] == team_id and row["status"] == "success"),
            "split_windows": sum(1 for row in MANIFEST if row["team_id"] == team_id and row["status"] == "split"),
            "failed_windows": sum(1 for row in MANIFEST if row["team_id"] == team_id and row["status"] == "failed"),
            "unique_lineups": 0, "lineup_seconds": 0, "team_rebounds": 0,
            "opponent_rebounds": 0, "player_rows": 0, "control_validated": True,
        }
    lineup_totals = totals(lineups.values())
    opponent_totals = totals(opponents.values())
    try:
        team_control = one_control_row(fetch(team_id, "Team", START, END, "season-control"), "Team control")
        opponent_control = one_control_row(fetch(team_id, "TeamOpponent", START, END, "season-control"), "TeamOpponent control")
        errors.extend(compare_control(lineup_totals, team_control, "team control"))
        errors.extend(compare_control(opponent_totals, opponent_control, "opponent control"))
        control_validated = not any("control" in error for error in errors)
    except Exception as exc:
        errors.append(f"season control failed: {exc!r}")
        control_validated = False
    players: dict[str, dict[str, Any]] = {}
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
        opp_seconds = num(opponent.get("SecondsPlayed"), num(opponent.get("Minutes")) * 60)
        if abs(seconds - opp_seconds) > 1e-6:
            errors.append(f"Aggregated seconds mismatch {lineup_id}")
            continue
        for player_id, name in zip(ids, names, strict=True):
            item = players.setdefault(player_id, {
                "names": Counter(), "seconds": 0.0, "team_rebounds": 0.0,
                "opponent_rebounds": 0.0, "team_points": 0.0,
                "opponent_points": 0.0, "team_off_poss": 0.0,
                "opponent_off_poss": 0.0,
            })
            item["names"][name] += seconds
            item["seconds"] += seconds
            item["team_rebounds"] += num(row.get("Rebounds"))
            item["opponent_rebounds"] += num(opponent.get("Rebounds"))
            item["team_points"] += num(row.get("Points"))
            item["opponent_points"] += num(opponent.get("Points"))
            item["team_off_poss"] += num(row.get("OffPoss"))
            item["opponent_off_poss"] += num(opponent.get("OffPoss"))
    player_seconds = sum(item["seconds"] for item in players.values())
    player_team_rebounds = sum(item["team_rebounds"] for item in players.values())
    player_opp_rebounds = sum(item["opponent_rebounds"] for item in players.values())
    if abs(player_seconds - 5 * lineup_totals["SecondsPlayed"]) > 1e-6:
        errors.append("five-player seconds identity failed")
    if abs(player_team_rebounds - 5 * lineup_totals["Rebounds"]) > 1e-6:
        errors.append("five-player team rebound identity failed")
    if abs(player_opp_rebounds - 5 * opponent_totals["Rebounds"]) > 1e-6:
        errors.append("five-player opponent rebound identity failed")
    player_rows: list[dict[str, Any]] = []
    for player_id, item in players.items():
        rebound_events = item["team_rebounds"] + item["opponent_rebounds"]
        player_rows.append({
            "player_id": player_id, "player": item["names"].most_common(1)[0][0],
            "season": SEASON, "team_id": team_id, "seconds": item["seconds"],
            "minutes": item["seconds"] / 60, "team_rebounds": item["team_rebounds"],
            "opponent_rebounds": item["opponent_rebounds"],
            "team_trb_pct": 100 * item["team_rebounds"] / rebound_events if rebound_events else "",
            "team_points": item["team_points"], "opponent_points": item["opponent_points"],
            "team_off_poss": item["team_off_poss"], "opponent_off_poss": item["opponent_off_poss"],
        })
    status = {
        "season": SEASON, "group": GROUP, "team_id": team_id,
        "expected_active": active, "validated": not errors, "errors": " | ".join(errors),
        "leaf_windows": sum(1 for row in MANIFEST if row["team_id"] == team_id and row["status"] == "success"),
        "split_windows": sum(1 for row in MANIFEST if row["team_id"] == team_id and row["status"] == "split"),
        "failed_windows": sum(1 for row in MANIFEST if row["team_id"] == team_id and row["status"] == "failed"),
        "unique_lineups": len(lineups), "lineup_seconds": lineup_totals["SecondsPlayed"],
        "team_rebounds": lineup_totals["Rebounds"], "opponent_rebounds": opponent_totals["Rebounds"],
        "player_rows": len(player_rows), "control_validated": control_validated,
    }
    return player_rows, status


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    print(f"FULL BATCH season={SEASON} group={GROUP} dates={START}..{END} teams={TEAM_IDS}", flush=True)
    player_rows: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for team_id in TEAM_IDS:
        print(f"=== TEAM {team_id} ===", flush=True)
        pairs: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        windows_ok = True
        for window_start, window_end in initial_windows():
            window_pairs, window_ok = adaptive(team_id, window_start, window_end)
            pairs.extend(window_pairs)
            windows_ok = windows_ok and window_ok
        rows, status = aggregate_team(team_id, pairs, windows_ok)
        player_rows.extend(rows)
        statuses.append(status)
        print(f"TEAM RESULT {team_id}: validated={status['validated']} errors={status['errors']}", flush=True)
    write_csv(OUT / "player_team_batch.csv", player_rows)
    write_csv(OUT / "team_status.csv", statuses)
    write_csv(OUT / "request_audit.csv", AUDIT)
    write_csv(OUT / "window_manifest.csv", MANIFEST)
    complete = all(bool(row["validated"]) for row in statuses)
    metadata = {
        "generated_at_utc": now(), "season": SEASON, "season_type": "Regular Season",
        "group": GROUP, "from_date": START.isoformat(), "to_date": END.isoformat(),
        "team_ids": TEAM_IDS, "top_window_days": TOP_DAYS,
        "teams_validated": sum(bool(row["validated"]) for row in statuses),
        "teams_expected": len(statuses), "player_team_rows": len(player_rows),
        "request_attempts": len(AUDIT), "manifest_rows": len(MANIFEST), "complete": complete,
    }
    (OUT / "batch_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    if complete:
        (OUT / "batch_complete.json").write_text(
            json.dumps({"complete": True, "season": SEASON, "group": GROUP}, indent=2),
            encoding="utf-8",
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
