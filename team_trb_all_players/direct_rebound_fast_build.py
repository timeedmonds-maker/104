from __future__ import annotations

import csv
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
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
CHECKPOINT_ROOT = BASE / "direct_rebound_checkpoints"
OUTPUT_ROOT = BASE / "direct_rebound_output"
STATUS_COMMENT_ID = os.getenv("TEAM_TRB_STATUS_COMMENT_ID", "5161464311")
WORKERS = max(1, min(4, int(os.getenv("DIRECT_TRB_WORKERS", "2"))))
COMMIT_EVERY = max(1, int(os.getenv("DIRECT_TRB_COMMIT_EVERY", "12")))
REQUEST_PAUSE = max(0.0, float(os.getenv("DIRECT_TRB_REQUEST_PAUSE", "0.20")))
MIN_SECONDS = 10_000 * 60
TOTALS_URL = "https://api.pbpstats.com/get-totals/nba"
ON_OFF_STAT_URL = "https://api.pbpstats.com/get-on-off/nba/stat"
TEAM_IDS = [
    1610612737, 1610612738, 1610612739, 1610612740, 1610612741,
    1610612742, 1610612743, 1610612744, 1610612745, 1610612746,
    1610612747, 1610612748, 1610612749, 1610612750, 1610612751,
    1610612752, 1610612753, 1610612754, 1610612755, 1610612756,
    1610612757, 1610612758, 1610612759, 1610612760, 1610612761,
    1610612762, 1610612763, 1610612764, 1610612765, 1610612766,
]
STATS = ("OffRebounds", "DefRebounds", "OffReboundPct", "DefReboundPct")
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def season_name(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


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


def request_json(url: str, params: dict[str, str], attempts: int = 6) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(HEADERS)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = session.get(url, params=params, timeout=(10, 75))
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]!r}")
            if response.status_code == 400:
                return {
                    "ok": False,
                    "absent": True,
                    "status_code": 400,
                    "url": response.url,
                    "errors": [response.text[:300]],
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
                time.sleep(min(2 ** (attempt - 1), 12) + random.random())
    return {"ok": False, "absent": False, "errors": errors}


def totals_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("multi_row_table_data")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def stat_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def rebound_candidates(own: int, displayed_pct: Any, max_opp: int) -> list[int]:
    pct_number = number(displayed_pct)
    if pct_number is None or own < 0 or max_opp < 0:
        return []
    if own == 0:
        # A displayed 0.000 rate does not identify the opponent denominator.
        return []
    if pct_number <= 0 or pct_number > 1:
        return []

    p = Decimal(str(displayed_pct))
    half = Decimal("0.0005")
    low = max(Decimal("0"), p - half)
    high = min(Decimal("1"), p + half)
    own_d = Decimal(own)
    if high <= 0:
        return []
    lower_raw = own_d / high - own_d
    lower = max(0, math.ceil(float(lower_raw) - 1e-12))
    if low <= 0:
        upper = max_opp
    else:
        upper_raw = own_d / low - own_d
        upper = min(max_opp, math.floor(float(upper_raw) + 1e-12))
    if upper < lower:
        return []

    result: list[int] = []
    for opponent in range(lower, upper + 1):
        ratio = own / (own + opponent)
        if abs(ratio - pct_number) <= 0.0005000001:
            result.append(opponent)
    return result


def index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in rows:
        key = normalize_name(str(row.get("Name") or ""))
        if not key:
            continue
        if key in indexed:
            duplicates.add(key)
        else:
            indexed[key] = row
    for key in duplicates:
        indexed.pop(key, None)
    return indexed


def task_path(season: str, team_id: int) -> Path:
    return CHECKPOINT_ROOT / season / f"{team_id}.json"


def checkpoint_complete(season: str, team_id: int) -> bool:
    path = task_path(season, team_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("complete") is True and data.get("season") == season and data.get("team_id") == team_id


def fetch_task(season: str, team_id: int) -> dict[str, Any]:
    started = time.monotonic()
    common = {"Season": season, "SeasonType": "Regular Season", "TeamId": str(team_id)}
    player_result = request_json(TOTALS_URL, {**common, "Type": "Player"})
    if not player_result.get("ok"):
        if player_result.get("absent"):
            return {
                "complete": True,
                "season": season,
                "team_id": team_id,
                "absent_team_season": True,
                "players": [],
                "requests": {"players": player_result},
                "generated_at_utc": now(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        return {
            "complete": False,
            "season": season,
            "team_id": team_id,
            "error": "player totals request failed",
            "requests": {"players": player_result},
            "generated_at_utc": now(),
        }

    player_rows = totals_rows(player_result.get("payload"))
    if not player_rows:
        return {
            "complete": True,
            "season": season,
            "team_id": team_id,
            "absent_team_season": True,
            "players": [],
            "requests": {"players": {k: v for k, v in player_result.items() if k != "payload"}},
            "generated_at_utc": now(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    stat_results: dict[str, dict[str, Any]] = {}
    for stat in STATS:
        result = request_json(ON_OFF_STAT_URL, {**common, "Stat": stat})
        stat_results[stat] = result
        if not result.get("ok"):
            return {
                "complete": False,
                "season": season,
                "team_id": team_id,
                "error": f"{stat} request failed",
                "requests": {
                    "players": {k: v for k, v in player_result.items() if k != "payload"},
                    **{name: {k: v for k, v in item.items() if k != "payload"} for name, item in stat_results.items()},
                },
                "generated_at_utc": now(),
            }

    indexed = {stat: index_rows(stat_rows(result.get("payload"))) for stat, result in stat_results.items()}
    output_rows: list[dict[str, Any]] = []
    validation_errors: list[str] = []

    for player in player_rows:
        seconds = number(player.get("SecondsPlayed"))
        if seconds is None or seconds <= 0:
            continue
        name = str(player.get("Name") or player.get("ShortName") or "").strip()
        player_id = str(player.get("EntityId") or player.get("RowId") or "").strip()
        key = normalize_name(name)
        if not name or not player_id or not key:
            validation_errors.append(f"invalid player identity: {player!r}")
            continue
        matched = {stat: indexed[stat].get(key) for stat in STATS}
        if any(row is None for row in matched.values()):
            validation_errors.append(f"missing on-off rows for {player_id} {name}")
            continue

        team_oreb = integer(matched["OffRebounds"].get("On"))
        team_dreb = integer(matched["DefRebounds"].get("On"))
        if team_oreb is None or team_dreb is None:
            validation_errors.append(f"non-integer rebound count for {player_id} {name}")
            continue
        minutes_cap = max(20, math.ceil(seconds / 60 * 2.5 + 25))
        opp_dreb_candidates = rebound_candidates(
            team_oreb, matched["OffReboundPct"].get("On"), minutes_cap
        )
        opp_oreb_candidates = rebound_candidates(
            team_dreb, matched["DefReboundPct"].get("On"), minutes_cap
        )
        total_candidates = sorted({
            oreb + dreb
            for oreb in opp_oreb_candidates
            for dreb in opp_dreb_candidates
        })
        output_rows.append({
            "season": season,
            "team_id": team_id,
            "player_id": player_id,
            "player": name,
            "team_abbreviation": str(player.get("TeamAbbreviation") or ""),
            "seconds": seconds,
            "minutes_exact": seconds / 60,
            "minutes_on_displayed": number(matched["OffRebounds"].get("MinutesOn")),
            "team_off_rebounds": team_oreb,
            "team_def_rebounds": team_dreb,
            "team_rebounds": team_oreb + team_dreb,
            "off_rebound_pct_displayed": matched["OffReboundPct"].get("On"),
            "def_rebound_pct_displayed": matched["DefReboundPct"].get("On"),
            "opponent_def_rebound_candidates": opp_dreb_candidates,
            "opponent_off_rebound_candidates": opp_oreb_candidates,
            "opponent_rebound_candidates": total_candidates,
            "opponent_rebounds_exact": total_candidates[0] if len(total_candidates) == 1 else None,
            "exact": len(total_candidates) == 1,
        })

    request_summary = {
        "players": {k: v for k, v in player_result.items() if k != "payload"},
        **{
            stat: {k: v for k, v in result.items() if k != "payload"}
            for stat, result in stat_results.items()
        },
    }
    return {
        "complete": not validation_errors,
        "season": season,
        "team_id": team_id,
        "absent_team_season": False,
        "players": output_rows,
        "validation_errors": validation_errors,
        "requests": request_summary,
        "generated_at_utc": now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def write_task(result: dict[str, Any]) -> None:
    season = str(result["season"])
    team_id = int(result["team_id"])
    path = task_path(season, team_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def git_commit_progress(label: str) -> bool:
    subprocess.run(["git", "add", "-A", "--", str(CHECKPOINT_ROOT), str(OUTPUT_ROOT)], cwd=REPO, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if diff.returncode == 0:
        return False
    subprocess.run(["git", "commit", "-m", f"Direct rebound build progress: {label}"], cwd=REPO, check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=REPO, check=True)
    return True


def update_status(body: str) -> None:
    try:
        subprocess.run([
            "gh", "api", "--method", "PATCH",
            f"repos/timeedmonds-maker/104/issues/comments/{STATUS_COMMENT_ID}",
            "-f", f"body={body}",
        ], cwd=REPO, check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[{now()}] status update failed: {exc}", flush=True)


def read_all_tasks() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(CHECKPOINT_ROOT.glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("complete") is True:
            rows.extend(row for row in data.get("players", []) if isinstance(row, dict))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def aggregate() -> dict[str, Any]:
    segment_rows = read_all_tasks()
    seconds_by_player: dict[str, float] = defaultdict(float)
    names: dict[str, Counter[str]] = defaultdict(Counter)
    for row in segment_rows:
        player_id = str(row["player_id"])
        seconds = float(row["seconds"])
        seconds_by_player[player_id] += seconds
        names[player_id][str(row["player"])] += seconds
    qualifiers = {player_id for player_id, seconds in seconds_by_player.items() if seconds >= MIN_SECONDS}

    qualifying_segments: list[dict[str, Any]] = []
    fallback_keys: set[tuple[str, int]] = set()
    for row in segment_rows:
        player_id = str(row["player_id"])
        if player_id not in qualifiers:
            continue
        candidates = row.get("opponent_rebound_candidates") or []
        exact = len(candidates) == 1
        if not exact:
            fallback_keys.add((str(row["season"]), int(row["team_id"])))
        qualifying_segments.append({
            "season": row["season"],
            "team_id": row["team_id"],
            "player_id": player_id,
            "player": row["player"],
            "seconds": row["seconds"],
            "minutes": float(row["seconds"]) / 60,
            "team_rebounds": row["team_rebounds"],
            "opponent_rebounds": candidates[0] if exact else "",
            "exact": exact,
            "opponent_rebound_candidates": json.dumps(candidates, separators=(",", ":")),
        })

    careers: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "seconds": 0.0, "team_rebounds": 0, "opponent_rebounds": 0,
        "seasons": set(), "teams": set(), "exact": True,
    })
    for row in qualifying_segments:
        item = careers[str(row["player_id"])]
        item["seconds"] += float(row["seconds"])
        item["team_rebounds"] += int(row["team_rebounds"])
        if row["exact"]:
            item["opponent_rebounds"] += int(row["opponent_rebounds"])
        else:
            item["exact"] = False
        item["seasons"].add(str(row["season"]))
        item["teams"].add(str(row["team_id"]))

    leaderboard: list[dict[str, Any]] = []
    for player_id, item in careers.items():
        if not item["exact"]:
            continue
        denominator = item["team_rebounds"] + item["opponent_rebounds"]
        leaderboard.append({
            "player_id": player_id,
            "player": names[player_id].most_common(1)[0][0],
            "minutes": item["seconds"] / 60,
            "seconds": item["seconds"],
            "team_rebounds": item["team_rebounds"],
            "opponent_rebounds": item["opponent_rebounds"],
            "team_trb_pct": 100 * item["team_rebounds"] / denominator if denominator else "",
            "season_count": len(item["seasons"]),
            "team_count": len(item["teams"]),
        })
    leaderboard.sort(key=lambda row: (-float(row["team_trb_pct"]), -float(row["minutes"]), row["player"]))
    for rank, row in enumerate(leaderboard, 1):
        row["rank"] = rank
    leaderboard = [{"rank": row.pop("rank"), **row} for row in leaderboard]

    fallback_rows = [{"season": season, "team_id": team_id} for season, team_id in sorted(fallback_keys)]
    write_csv(OUTPUT_ROOT / "qualifying_player_team_segments.csv", qualifying_segments)
    write_csv(OUTPUT_ROOT / "fallback_team_seasons.csv", fallback_rows)
    write_csv(OUTPUT_ROOT / "career_team_trb_10000_minutes_exact_so_far.csv", leaderboard)
    summary = {
        "generated_at_utc": now(),
        "team_season_checkpoints": len(list(CHECKPOINT_ROOT.glob("*/*.json"))),
        "player_team_segments": len(segment_rows),
        "qualifying_players": len(qualifiers),
        "qualifying_segments": len(qualifying_segments),
        "exact_qualifying_players": len(leaderboard),
        "fallback_team_seasons": len(fallback_rows),
        "complete": len(fallback_rows) == 0,
        "minimum_minutes": 10_000,
    }
    (OUTPUT_ROOT / "build_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if summary["complete"]:
        (OUTPUT_ROOT / "career_team_trb_10000_minutes.csv").write_text(
            (OUTPUT_ROOT / "career_team_trb_10000_minutes_exact_so_far.csv").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return summary


def main() -> int:
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "config", "user.name", "github-codespaces[bot]"], cwd=REPO, check=True)
    subprocess.run(["git", "config", "user.email", "codespaces@users.noreply.github.com"], cwd=REPO, check=True)

    pending = [
        (season_name(year), team_id)
        for year in range(2000, 2026)
        for team_id in TEAM_IDS
        if not checkpoint_complete(season_name(year), team_id)
    ]
    total = 26 * len(TEAM_IDS)
    done = total - len(pending)
    print(f"[{now()}] Direct rebound build starting: {done}/{total} complete, workers={WORKERS}", flush=True)

    while pending:
        batch = pending[: max(WORKERS, COMMIT_EVERY)]
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_task, season, team_id): (season, team_id) for season, team_id in batch}
            for future in as_completed(futures):
                season, team_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "complete": False, "season": season, "team_id": team_id,
                        "error": repr(exc), "generated_at_utc": now(),
                    }
                write_task(result)
                results.append(result)
                print(
                    f"[{now()}] {season}-{team_id} complete={result.get('complete')} "
                    f"players={len(result.get('players', []))} elapsed={result.get('elapsed_seconds')}",
                    flush=True,
                )
        successful = sum(1 for result in results if result.get("complete") is True)
        failed = len(results) - successful
        git_commit_progress(f"{batch[0][0]}-{batch[0][1]} through {batch[-1][0]}-{batch[-1][1]}")
        pending = [
            (season_name(year), team_id)
            for year in range(2000, 2026)
            for team_id in TEAM_IDS
            if not checkpoint_complete(season_name(year), team_id)
        ]
        done = total - len(pending)
        update_status(
            "**Direct team rebound build**\n\n"
            f"Progress: **{done}/{total}** team-seasons downloaded.\n\n"
            f"Latest batch: {successful} complete, {failed} retained for retry.\n\n"
            f"Pending: {len(pending)}. Workers: {WORKERS}. Updated: {now()}."
        )
        if failed and successful == 0:
            print("No tasks succeeded in this batch; stopping to avoid an infinite retry loop.", flush=True)
            return 1

    summary = aggregate()
    git_commit_progress("aggregate direct rebound leaderboard")
    update_status(
        "**Direct team rebound build complete**\n\n"
        f"Progress: **780/780** team-seasons downloaded.\n\n"
        f"Qualifying players: {summary['qualifying_players']}. "
        f"Exact without fallback: {summary['exact_qualifying_players']}.\n\n"
        f"Targeted fallback team-seasons required: {summary['fallback_team_seasons']}. "
        f"Updated: {now()}."
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
