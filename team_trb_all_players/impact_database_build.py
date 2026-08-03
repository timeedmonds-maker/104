from __future__ import annotations

import csv
import gzip
import json
import math
import os
import random
import subprocess
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import requests

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
DB_ROOT = BASE / "impact_database"
CORE_ROOT = DB_ROOT / "core_checkpoints"
PAIR_ROOT = DB_ROOT / "pair_checkpoints"
OUTPUT_ROOT = DB_ROOT / "outputs"
STATUS_COMMENT_ID = os.getenv("TEAM_TRB_STATUS_COMMENT_ID", "5161464311")
CORE_WORKERS = max(1, min(4, int(os.getenv("IMPACT_DB_CORE_WORKERS", "2"))))
PAIR_WORKERS = max(1, min(3, int(os.getenv("IMPACT_DB_PAIR_WORKERS", "2"))))
CORE_COMMIT_EVERY = max(1, int(os.getenv("IMPACT_DB_CORE_COMMIT_EVERY", "12")))
PAIR_COMMIT_EVERY = max(1, int(os.getenv("IMPACT_DB_PAIR_COMMIT_EVERY", "4")))
REQUEST_PAUSE = max(0.0, float(os.getenv("IMPACT_DB_REQUEST_PAUSE", "0.20")))
STALL_PAUSE = max(30, int(os.getenv("IMPACT_DB_STALL_PAUSE", "180")))
MIN_SECONDS = 10_000 * 60

TOTALS_URL = "https://api.pbpstats.com/get-totals/nba"
ON_OFF_TEAM_URL = "https://api.pbpstats.com/get-on-off/nba/team"
ON_OFF_PLAYER_URL = "https://api.pbpstats.com/get-on-off/nba/player"

TEAM_IDS = [
    1610612737, 1610612738, 1610612739, 1610612740, 1610612741,
    1610612742, 1610612743, 1610612744, 1610612745, 1610612746,
    1610612747, 1610612748, 1610612749, 1610612750, 1610612751,
    1610612752, 1610612753, 1610612754, 1610612755, 1610612756,
    1610612757, 1610612758, 1610612759, 1610612760, 1610612761,
    1610612762, 1610612763, 1610612764, 1610612765, 1610612766,
]
SEASONS = [f"{year}-{str(year + 1)[-2:]}" for year in range(2000, 2026)]
EXPECTED_TEAM_SEASONS = len(SEASONS) * len(TEAM_IDS)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any) -> int | None:
    result = number(value)
    if result is None or abs(result - round(result)) > 1e-6:
        return None
    return int(round(result))


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(child) for child in value]
    return str(value)


def write_gzip_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(json_safe(value), handle, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def read_gzip_json(path: Path) -> dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def request_json(url: str, params: dict[str, str], attempts: int = 6) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(HEADERS)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = session.get(url, params=params, timeout=(10, 90))
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:180]!r}")
            if response.status_code == 400:
                return {
                    "ok": False,
                    "absent": True,
                    "status_code": 400,
                    "url": response.url,
                    "errors": [response.text[:500]],
                }
            response.raise_for_status()
            payload = response.json()
            time.sleep(REQUEST_PAUSE)
            return {
                "ok": True,
                "absent": False,
                "status_code": response.status_code,
                "url": response.url,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "payload": payload,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc!r}")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 15) + random.random())
    return {"ok": False, "absent": False, "errors": errors}


def response_meta(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "payload"}


def payload_meta(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if key not in {"results", "multi_row_table_data"}}


def totals_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("multi_row_table_data")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def results_map(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    results = payload.get("results")
    if not isinstance(results, dict):
        return {}
    output: dict[str, list[dict[str, Any]]] = {}
    for metric, rows in results.items():
        if isinstance(rows, list):
            output[str(metric)] = [row for row in rows if isinstance(row, dict)]
    return output


def player_identity(row: dict[str, Any]) -> tuple[str, str]:
    player_id = str(row.get("EntityId") or row.get("RowId") or row.get("PlayerId") or "").strip()
    name = str(row.get("Name") or row.get("ShortName") or "").strip()
    return player_id, name


def build_name_index(player_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in player_rows:
        player_id, name = player_identity(row)
        key = normalize_name(name)
        if not player_id or not key:
            continue
        if key in index:
            duplicates.add(key)
        else:
            index[key] = row
    for key in duplicates:
        index.pop(key, None)
    return index


def flatten_metrics(
    payload: Any,
    season: str,
    team_id: int,
    name_index: dict[str, dict[str, Any]],
    focal_player_id: str = "",
    focal_player_name: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric, metric_rows in results_map(payload).items():
        for row in metric_rows:
            subject_name = str(row.get("Name") or "").strip()
            mapped = name_index.get(normalize_name(subject_name), {})
            subject_id, canonical_name = player_identity(mapped)
            rows.append({
                "season": season,
                "team_id": team_id,
                "focal_player_id": focal_player_id,
                "focal_player": focal_player_name,
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
    return rows


def rebound_candidates(own: int, displayed_pct: Any, max_opponent: int) -> list[int]:
    pct_number = number(displayed_pct)
    if pct_number is None or own <= 0 or pct_number <= 0 or pct_number > 1:
        return []
    p = Decimal(str(displayed_pct))
    half = Decimal("0.0005")
    low = max(Decimal("0"), p - half)
    high = min(Decimal("1"), p + half)
    own_d = Decimal(own)
    lower = max(0, math.ceil(float(own_d / high - own_d) - 1e-12))
    if low <= 0:
        upper = max_opponent
    else:
        upper = min(max_opponent, math.floor(float(own_d / low - own_d) + 1e-12))
    if upper < lower:
        return []
    return [
        opponent
        for opponent in range(lower, upper + 1)
        if abs(own / (own + opponent) - pct_number) <= 0.0005000001
    ]


def metric_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("metric") or ""), str(row.get("subject_player_id") or ""))
        if key[0] and key[1]:
            output[key] = row
    return output


def derive_rebounds(
    player_rows: list[dict[str, Any]],
    team_metric_rows: list[dict[str, Any]],
    season: str,
    team_id: int,
) -> list[dict[str, Any]]:
    lookup = metric_index(team_metric_rows)
    output: list[dict[str, Any]] = []
    for player in player_rows:
        player_id, name = player_identity(player)
        seconds = number(player.get("SecondsPlayed"))
        if not player_id or not name or seconds is None or seconds <= 0:
            continue
        entries = {metric: lookup.get((metric, player_id)) for metric in (
            "OffRebounds", "DefRebounds", "OffReboundPct", "DefReboundPct"
        )}
        if any(value is None for value in entries.values()):
            output.append({
                "season": season,
                "team_id": team_id,
                "player_id": player_id,
                "player": name,
                "seconds": seconds,
                "exact": False,
                "error": "required rebound metrics missing",
            })
            continue
        team_oreb = integer(entries["OffRebounds"].get("on"))
        team_dreb = integer(entries["DefRebounds"].get("on"))
        if team_oreb is None or team_dreb is None:
            output.append({
                "season": season,
                "team_id": team_id,
                "player_id": player_id,
                "player": name,
                "seconds": seconds,
                "exact": False,
                "error": "non-integer team rebound count",
            })
            continue
        cap = max(25, math.ceil(seconds / 60 * 2.5 + 30))
        opponent_dreb = rebound_candidates(team_oreb, entries["OffReboundPct"].get("on"), cap)
        opponent_oreb = rebound_candidates(team_dreb, entries["DefReboundPct"].get("on"), cap)
        totals = sorted({oreb + dreb for oreb in opponent_oreb for dreb in opponent_dreb})
        output.append({
            "season": season,
            "team_id": team_id,
            "player_id": player_id,
            "player": name,
            "seconds": seconds,
            "minutes": seconds / 60,
            "team_off_rebounds": team_oreb,
            "team_def_rebounds": team_dreb,
            "team_rebounds": team_oreb + team_dreb,
            "off_rebound_pct_displayed": entries["OffReboundPct"].get("on"),
            "def_rebound_pct_displayed": entries["DefReboundPct"].get("on"),
            "opponent_off_rebound_candidates": opponent_oreb,
            "opponent_def_rebound_candidates": opponent_dreb,
            "opponent_rebound_candidates": totals,
            "opponent_rebounds_exact": totals[0] if len(totals) == 1 else None,
            "exact": len(totals) == 1,
        })
    return output


def core_path(season: str, team_id: int) -> Path:
    return CORE_ROOT / season / f"{team_id}.json.gz"


def pair_path(season: str, team_id: int) -> Path:
    return PAIR_ROOT / season / f"{team_id}.json.gz"


def checkpoint_complete(path: Path, season: str, team_id: int) -> bool:
    data = read_gzip_json(path)
    return data.get("complete") is True and data.get("season") == season and data.get("team_id") == team_id


def core_complete(season: str, team_id: int) -> bool:
    return checkpoint_complete(core_path(season, team_id), season, team_id)


def pair_complete(season: str, team_id: int) -> bool:
    return checkpoint_complete(pair_path(season, team_id), season, team_id)


def prior_attempts(path: Path) -> int:
    data = read_gzip_json(path)
    try:
        return max(0, int(data.get("attempts", 0)))
    except (TypeError, ValueError):
        return 0


def fetch_core_task(season: str, team_id: int) -> dict[str, Any]:
    path = core_path(season, team_id)
    attempts = prior_attempts(path) + 1
    started = time.monotonic()
    common = {"Season": season, "SeasonType": "Regular Season", "TeamId": str(team_id)}

    totals_result = request_json(TOTALS_URL, {**common, "Type": "Player"})
    if not totals_result.get("ok"):
        if totals_result.get("absent"):
            result = {
                "complete": True,
                "absent_team_season": True,
                "season": season,
                "team_id": team_id,
                "attempts": attempts,
                "player_totals": [],
                "team_on_off_results": {},
                "rebound_derived": [],
                "requests": {"player_totals": response_meta(totals_result)},
                "generated_at_utc": now(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            write_gzip_json(path, result)
            return result
        result = {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "error": "player totals request failed",
            "requests": {"player_totals": response_meta(totals_result)},
            "generated_at_utc": now(),
        }
        write_gzip_json(path, result)
        return result

    player_rows = totals_rows(totals_result.get("payload"))
    if not player_rows:
        result = {
            "complete": True,
            "absent_team_season": True,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "player_totals": [],
            "team_on_off_results": {},
            "rebound_derived": [],
            "requests": {"player_totals": response_meta(totals_result)},
            "payload_meta": {"player_totals": payload_meta(totals_result.get("payload"))},
            "generated_at_utc": now(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_gzip_json(path, result)
        return result

    team_result = request_json(ON_OFF_TEAM_URL, common)
    if not team_result.get("ok"):
        result = {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "error": "team on-off request failed",
            "requests": {
                "player_totals": response_meta(totals_result),
                "team_on_off": response_meta(team_result),
            },
            "generated_at_utc": now(),
        }
        write_gzip_json(path, result)
        return result

    enriched_players: list[dict[str, Any]] = []
    for row in player_rows:
        enriched_players.append({"season": season, "team_id": team_id, **row})
    name_index = build_name_index(player_rows)
    team_results = results_map(team_result.get("payload"))
    if not team_results:
        result = {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "error": "team on-off response contained no metric results",
            "requests": {
                "player_totals": response_meta(totals_result),
                "team_on_off": response_meta(team_result),
            },
            "payload_meta": {
                "player_totals": payload_meta(totals_result.get("payload")),
                "team_on_off": payload_meta(team_result.get("payload")),
            },
            "generated_at_utc": now(),
        }
        write_gzip_json(path, result)
        return result
    team_rows = flatten_metrics({"results": team_results}, season, team_id, name_index)
    rebound_rows = derive_rebounds(player_rows, team_rows, season, team_id)

    unmapped = sorted({
        str(row.get("subject_player"))
        for row in team_rows
        if not row.get("subject_player_id") and row.get("subject_player")
    })
    result = {
        "complete": True,
        "absent_team_season": False,
        "season": season,
        "team_id": team_id,
        "attempts": attempts,
        "player_totals": enriched_players,
        "team_on_off_results": team_results,
        "rebound_derived": rebound_rows,
        "unmapped_team_on_off_names": unmapped,
        "requests": {
            "player_totals": response_meta(totals_result),
            "team_on_off": response_meta(team_result),
        },
        "payload_meta": {
            "player_totals": payload_meta(totals_result.get("payload")),
            "team_on_off": payload_meta(team_result.get("payload")),
        },
        "generated_at_utc": now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    write_gzip_json(path, result)
    return result


def fetch_pair_task(season: str, team_id: int) -> dict[str, Any]:
    path = pair_path(season, team_id)
    existing = read_gzip_json(path)
    attempts = max(0, int(existing.get("attempts", 0) or 0)) + 1
    core = read_gzip_json(core_path(season, team_id))
    if core.get("complete") is not True:
        result = {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "error": "core checkpoint incomplete",
            "generated_at_utc": now(),
        }
        write_gzip_json(path, result)
        return result
    if core.get("absent_team_season") is True:
        result = {
            "complete": True,
            "absent_team_season": True,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "focal_players": [],
            "generated_at_utc": now(),
        }
        write_gzip_json(path, result)
        return result

    player_rows = [row for row in core.get("player_totals", []) if isinstance(row, dict)]
    name_index = build_name_index(player_rows)
    completed_ids = {
        str(value)
        for value in existing.get("completed_player_ids", [])
        if str(value)
    }
    focal_records = [
        row for row in existing.get("focal_players", [])
        if isinstance(row, dict)
    ]
    errors = [
        row for row in existing.get("errors", [])
        if isinstance(row, dict)
    ]

    for player in player_rows:
        player_id, player_name = player_identity(player)
        seconds = number(player.get("SecondsPlayed"))
        if not player_id or not player_name or seconds is None or seconds <= 0 or player_id in completed_ids:
            continue
        params = {
            "Season": season,
            "SeasonType": "Regular Season",
            "TeamId": str(team_id),
            "PlayerId": player_id,
        }
        fetched = request_json(ON_OFF_PLAYER_URL, params)
        if not fetched.get("ok"):
            errors.append({
                "player_id": player_id,
                "player": player_name,
                "request": response_meta(fetched),
                "at_utc": now(),
            })
            partial = {
                "complete": False,
                "season": season,
                "team_id": team_id,
                "attempts": attempts,
                "completed_player_ids": sorted(completed_ids),
                "focal_players": focal_records,
                "errors": errors[-100:],
                "generated_at_utc": now(),
            }
            write_gzip_json(path, partial)
            continue

        player_results = results_map(fetched.get("payload"))
        if not player_results:
            errors.append({
                "player_id": player_id,
                "player": player_name,
                "request": response_meta(fetched),
                "error": "player on-off response contained no metric results",
                "at_utc": now(),
            })
            write_gzip_json(path, {
                "complete": False,
                "season": season,
                "team_id": team_id,
                "attempts": attempts,
                "completed_player_ids": sorted(completed_ids),
                "focal_players": focal_records,
                "errors": errors[-100:],
                "generated_at_utc": now(),
            })
            continue
        focal_records.append({
            "season": season,
            "team_id": team_id,
            "focal_player_id": player_id,
            "focal_player": player_name,
            "seconds": seconds,
            "request": response_meta(fetched),
            "payload_meta": payload_meta(fetched.get("payload")),
            "metric_count": len(player_results),
            "row_count": sum(len(rows) for rows in player_results.values()),
            "results": player_results,
        })
        completed_ids.add(player_id)
        partial = {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "attempts": attempts,
            "completed_player_ids": sorted(completed_ids),
            "focal_players": focal_records,
            "errors": errors[-100:],
            "generated_at_utc": now(),
        }
        write_gzip_json(path, partial)

    expected_ids = {
        player_identity(row)[0]
        for row in player_rows
        if player_identity(row)[0] and (number(row.get("SecondsPlayed")) or 0) > 0
    }
    complete = expected_ids.issubset(completed_ids)
    result = {
        "complete": complete,
        "absent_team_season": False,
        "season": season,
        "team_id": team_id,
        "attempts": attempts,
        "completed_player_ids": sorted(completed_ids),
        "expected_player_ids": sorted(expected_ids),
        "focal_players": focal_records,
        "errors": errors[-100:],
        "generated_at_utc": now(),
    }
    write_gzip_json(path, result)
    return result


def all_tasks(root: str) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    for season in SEASONS:
        for team_id in TEAM_IDS:
            path = core_path(season, team_id) if root == "core" else pair_path(season, team_id)
            complete = core_complete(season, team_id) if root == "core" else pair_complete(season, team_id)
            if not complete:
                rows.append((season, team_id, prior_attempts(path)))
    rows.sort(key=lambda item: (item[2], item[0], item[1]))
    return rows


def git_commit_progress(label: str) -> bool:
    paths = [str(DB_ROOT)]
    subprocess.run(["git", "add", "-A", "--", *paths], cwd=REPO, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if diff.returncode == 0:
        return False
    subprocess.run(
        ["git", "commit", "-m", f"Impact database progress: {label}"],
        cwd=REPO,
        check=True,
    )
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=REPO, check=True)
    return True


def update_status(body: str) -> None:
    command = [
        "gh", "api", "--method", "PATCH",
        f"repos/timeedmonds-maker/104/issues/comments/{STATUS_COMMENT_ID}",
        "-f", f"body={body}",
    ]
    try:
        subprocess.run(command, cwd=REPO, check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[{now()}] Status update failed: {exc}", flush=True)


def completed_count(stage: str) -> int:
    function = core_complete if stage == "core" else pair_complete
    return sum(function(season, team_id) for season in SEASONS for team_id in TEAM_IDS)


def run_stage(stage: str) -> None:
    worker_count = CORE_WORKERS if stage == "core" else PAIR_WORKERS
    commit_every = CORE_COMMIT_EVERY if stage == "core" else PAIR_COMMIT_EVERY
    function = fetch_core_task if stage == "core" else fetch_pair_task
    completed_since_commit = 0
    prior_complete = completed_count(stage)
    no_progress_rounds = 0

    while True:
        pending = all_tasks(stage)
        if not pending:
            git_commit_progress(f"{stage} complete")
            return
        batch = pending[:worker_count]
        print(
            f"[{now()}] {stage.upper()} {prior_complete}/{EXPECTED_TEAM_SEASONS}; "
            f"starting {', '.join(f'{s}-{t}' for s, t, _ in batch)}",
            flush=True,
        )
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {
                executor.submit(function, season, team_id): (season, team_id)
                for season, team_id, _ in batch
            }
            for future in as_completed(futures):
                season, team_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "complete": False,
                        "season": season,
                        "team_id": team_id,
                        "error": repr(exc),
                    }
                results.append(result)
                print(
                    f"[{now()}] {stage.upper()} {season}-{team_id} "
                    f"complete={result.get('complete')} error={result.get('error', '')}",
                    flush=True,
                )

        current_complete = completed_count(stage)
        gained = current_complete - prior_complete
        completed_since_commit += max(0, gained)
        if gained > 0:
            no_progress_rounds = 0
        else:
            no_progress_rounds += 1
        prior_complete = current_complete

        if completed_since_commit >= commit_every or current_complete == EXPECTED_TEAM_SEASONS:
            git_commit_progress(f"{stage} {current_complete} of {EXPECTED_TEAM_SEASONS}")
            completed_since_commit = 0

        other_complete = completed_count("pair" if stage == "core" else "core")
        update_status(
            "**NBA historical player-impact database build**\n\n"
            f"Core player/team on-off layer: **{current_complete if stage == 'core' else other_complete}"
            f"/{EXPECTED_TEAM_SEASONS}** team-seasons.\n\n"
            f"Teammate interaction layer: **{other_complete if stage == 'core' else current_complete}"
            f"/{EXPECTED_TEAM_SEASONS}** team-seasons.\n\n"
            f"Current stage: **{stage}**. Workers: {worker_count}. Updated: {now()}."
        )

        if no_progress_rounds >= max(4, worker_count * 2):
            print(f"[{now()}] No {stage} progress; pausing {STALL_PAUSE}s.", flush=True)
            git_commit_progress(f"{stage} retry state")
            time.sleep(STALL_PAUSE)
            no_progress_rounds = 0


def write_csv_gz(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            })
            count += 1
    return count


def aggregate_outputs() -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "generated_at_utc": now(),
        "expected_team_seasons": EXPECTED_TEAM_SEASONS,
        "core_complete": completed_count("core"),
        "pairs_complete": completed_count("pair"),
        "seasons": {},
    }
    careers: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "names": Counter(),
        "seconds": 0.0,
        "team_rebounds": 0,
        "opponent_min": 0,
        "opponent_max": 0,
        "ambiguous_segments": 0,
        "seasons": set(),
        "teams": set(),
    })

    for season in SEASONS:
        season_dir = OUTPUT_ROOT / season
        cores = [
            read_gzip_json(core_path(season, team_id))
            for team_id in TEAM_IDS
            if core_complete(season, team_id)
        ]
        pairs = [
            read_gzip_json(pair_path(season, team_id))
            for team_id in TEAM_IDS
            if pair_complete(season, team_id)
        ]
        player_rows = [
            row for core in cores for row in core.get("player_totals", [])
            if isinstance(row, dict)
        ]
        team_rows: list[dict[str, Any]] = []
        core_by_team: dict[int, dict[str, Any]] = {}
        for core in cores:
            team_id = int(core.get("team_id"))
            core_by_team[team_id] = core
            source_players = [
                row for row in core.get("player_totals", [])
                if isinstance(row, dict)
            ]
            team_rows.extend(flatten_metrics(
                {"results": core.get("team_on_off_results", {})},
                season,
                team_id,
                build_name_index(source_players),
            ))
        rebound_rows = [
            row for core in cores for row in core.get("rebound_derived", [])
            if isinstance(row, dict)
        ]
        pair_rows: list[dict[str, Any]] = []
        for pair in pairs:
            team_id = int(pair.get("team_id"))
            core = core_by_team.get(team_id, {})
            source_players = [
                row for row in core.get("player_totals", [])
                if isinstance(row, dict)
            ]
            name_index = build_name_index(source_players)
            for focal in pair.get("focal_players", []):
                if not isinstance(focal, dict):
                    continue
                pair_rows.extend(flatten_metrics(
                    {"results": focal.get("results", {})},
                    season,
                    team_id,
                    name_index,
                    focal_player_id=str(focal.get("focal_player_id") or ""),
                    focal_player_name=str(focal.get("focal_player") or ""),
                ))

        player_fields = sorted({key for row in player_rows for key in row})
        player_lead = [key for key in ("season", "team_id", "EntityId", "RowId", "Name", "ShortName", "SecondsPlayed") if key in player_fields]
        player_fields = player_lead + [key for key in player_fields if key not in player_lead]

        team_fields = [
            "season", "team_id", "subject_player_id", "subject_player", "metric",
            "minutes_on", "minutes_off", "on", "off", "on_off", "source_extra",
        ]
        rebound_fields = sorted({key for row in rebound_rows for key in row})
        pair_fields = [
            "season", "team_id", "focal_player_id", "focal_player",
            "subject_player_id", "subject_player", "metric",
            "minutes_on", "minutes_off", "on", "off", "on_off", "source_extra",
        ]

        counts = {
            "player_totals_rows": write_csv_gz(season_dir / "player_team_totals.csv.gz", player_rows, player_fields) if player_fields else 0,
            "team_on_off_rows": write_csv_gz(season_dir / "team_on_off_metrics.csv.gz", team_rows, team_fields),
            "rebound_rows": write_csv_gz(season_dir / "team_rebound_derived.csv.gz", rebound_rows, rebound_fields) if rebound_fields else 0,
            "pair_metric_rows": write_csv_gz(season_dir / "player_pair_metrics.csv.gz", pair_rows, pair_fields),
            "core_team_seasons": len(cores),
            "pair_team_seasons": len(pairs),
        }
        manifest["seasons"][season] = counts

        for row in rebound_rows:
            player_id = str(row.get("player_id") or "")
            seconds = number(row.get("seconds"))
            team_rebounds = integer(row.get("team_rebounds"))
            candidates = [
                int(value) for value in row.get("opponent_rebound_candidates", [])
                if integer(value) is not None
            ]
            if not player_id or seconds is None or team_rebounds is None or not candidates:
                continue
            item = careers[player_id]
            item["names"][str(row.get("player") or player_id)] += seconds
            item["seconds"] += seconds
            item["team_rebounds"] += team_rebounds
            item["opponent_min"] += min(candidates)
            item["opponent_max"] += max(candidates)
            if len(candidates) != 1:
                item["ambiguous_segments"] += 1
            item["seasons"].add(str(row.get("season") or ""))
            item["teams"].add(str(row.get("team_id") or ""))

    career_rows: list[dict[str, Any]] = []
    for player_id, item in careers.items():
        minutes = item["seconds"] / 60
        denominator_min = item["team_rebounds"] + item["opponent_min"]
        denominator_max = item["team_rebounds"] + item["opponent_max"]
        pct_high = 100 * item["team_rebounds"] / denominator_min if denominator_min else None
        pct_low = 100 * item["team_rebounds"] / denominator_max if denominator_max else None
        exact = item["opponent_min"] == item["opponent_max"]
        career_rows.append({
            "player_id": player_id,
            "player": item["names"].most_common(1)[0][0],
            "seconds": item["seconds"],
            "minutes": minutes,
            "team_rebounds": item["team_rebounds"],
            "opponent_rebounds_exact": item["opponent_min"] if exact else None,
            "opponent_rebounds_min": item["opponent_min"],
            "opponent_rebounds_max": item["opponent_max"],
            "team_trb_pct_exact": pct_low if exact else None,
            "team_trb_pct_min": pct_low,
            "team_trb_pct_max": pct_high,
            "exact": exact,
            "ambiguous_segments": item["ambiguous_segments"],
            "season_count": len(item["seasons"]),
            "team_count": len(item["teams"]),
            "seasons": ",".join(sorted(item["seasons"])),
            "team_ids": ",".join(sorted(item["teams"])),
            "qualifies_10000_minutes": minutes >= 10_000,
        })
    career_rows.sort(key=lambda row: (
        -(number(row.get("team_trb_pct_exact")) or number(row.get("team_trb_pct_min")) or -1),
        -(number(row.get("minutes")) or 0),
        str(row.get("player") or ""),
    ))
    rank = 0
    for row in career_rows:
        if row["qualifies_10000_minutes"]:
            rank += 1
            row["rank_10000_minutes"] = rank
        else:
            row["rank_10000_minutes"] = ""
    career_fields = [
        "rank_10000_minutes", "player_id", "player", "minutes", "seconds",
        "team_rebounds", "opponent_rebounds_exact", "opponent_rebounds_min",
        "opponent_rebounds_max", "team_trb_pct_exact", "team_trb_pct_min",
        "team_trb_pct_max", "exact", "ambiguous_segments", "season_count",
        "team_count", "seasons", "team_ids", "qualifies_10000_minutes",
    ]
    write_csv_gz(OUTPUT_ROOT / "career_team_trb_all_players.csv.gz", career_rows, career_fields)
    write_csv_gz(
        OUTPUT_ROOT / "career_team_trb_10000_minutes.csv.gz",
        [row for row in career_rows if row["qualifies_10000_minutes"]],
        career_fields,
    )

    manifest["career_players"] = len(career_rows)
    manifest["qualifying_players"] = sum(bool(row["qualifies_10000_minutes"]) for row in career_rows)
    manifest["complete"] = (
        manifest["core_complete"] == EXPECTED_TEAM_SEASONS
        and manifest["pairs_complete"] == EXPECTED_TEAM_SEASONS
    )
    (OUTPUT_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    git_commit_progress("aggregate outputs")
    return manifest


def main() -> int:
    stage = os.getenv("IMPACT_DB_STAGE", "all").strip().casefold()
    if stage not in {"all", "core", "pairs", "aggregate"}:
        raise SystemExit("IMPACT_DB_STAGE must be all, core, pairs, or aggregate")

    for directory in (CORE_ROOT, PAIR_ROOT, OUTPUT_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "config", "user.name", "github-codespaces[bot]"], cwd=REPO, check=True)
    subprocess.run(
        ["git", "config", "user.email", "codespaces@users.noreply.github.com"],
        cwd=REPO,
        check=True,
    )

    print(
        f"[{now()}] Impact database builder started. stage={stage} "
        f"core_workers={CORE_WORKERS} pair_workers={PAIR_WORKERS}",
        flush=True,
    )
    manifest: dict[str, Any] = {}
    if stage in {"all", "core"}:
        run_stage("core")
        manifest = aggregate_outputs()
    if stage in {"all", "pairs"}:
        if completed_count("core") != EXPECTED_TEAM_SEASONS:
            raise SystemExit("Core layer must be complete before pair layer")
        run_stage("pair")
        manifest = aggregate_outputs()
    if stage == "aggregate":
        manifest = aggregate_outputs()
    update_status(
        "**NBA historical player-impact database build**\n\n"
        f"Core player/team on-off layer: **{manifest['core_complete']}/{EXPECTED_TEAM_SEASONS}**.\n\n"
        f"Teammate interaction layer: **{manifest['pairs_complete']}/{EXPECTED_TEAM_SEASONS}**.\n\n"
        f"Aggregated outputs committed. Complete: **{manifest['complete']}**. Updated: {now()}."
    )
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    return 0 if manifest["complete"] or stage != "all" else 1


if __name__ == "__main__":
    raise SystemExit(main())
