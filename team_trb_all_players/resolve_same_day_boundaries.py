from __future__ import annotations

import argparse
import gzip
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
ROSTER = BASE / "impact_database" / "roster_tenure"
INPUT = ROSTER / "player_team_season_windows_schedule_audited.jsonl.gz"
OUTPUT = ROSTER / "player_team_season_windows_evidence_audited.jsonl.gz"
AUDIT = ROSTER / "same_day_evidence_audit.json"
SUMMARY = ROSTER / "same_day_evidence_summary.json"
CACHE = ROSTER / "player_game_log_cache"
GAME_LOG_URL = "https://api.pbpstats.com/get-game-logs/nba"
WORKERS = max(1, min(8, int(os.environ.get("TREB_STAGE1_PARTICIPATION_WORKERS", "4"))))
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}
_THREAD_LOCAL = threading.local()


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if text.isdigit() and text != "0" else ""


def ci(row: dict[str, Any], *keys: str) -> Any:
    lookup = {str(k).casefold(): v for k, v in row.items()}
    for key in keys:
        if key.casefold() in lookup:
            return lookup[key.casefold()]
    return None


def session() -> requests.Session:
    value = getattr(_THREAD_LOCAL, "session", None)
    if value is None:
        value = requests.Session()
        value.headers.update(HEADERS)
        _THREAD_LOCAL.session = value
    return value


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    """Find game-log row objects without depending on one response envelope shape."""
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        game_id = clean_id(ci(value, "GameId", "GameID", "game_id", "GAME_ID"))
        if game_id:
            found.append(value)
            return
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(payload)
    return found


def participation_pairs(payload: dict[str, Any]) -> set[tuple[str, int]]:
    """Return only positive (game_id, team_id) participation evidence.

    A returned game without an explicit team id is deliberately ignored: a traded player
    can appear for two teams in one season, so game id alone is not enough to establish
    which roster-tenure window owns the boundary game.
    """
    pairs: set[tuple[str, int]] = set()
    for row in candidate_rows(payload):
        game_id = clean_id(ci(row, "GameId", "GameID", "game_id", "GAME_ID"))
        team_id = clean_id(ci(row, "TeamId", "TeamID", "team_id", "TEAM_ID"))
        if game_id and team_id:
            pairs.add((game_id, int(team_id)))
    return pairs


def cache_path(season: str, player_id: str) -> Path:
    return CACHE / f"{season}__{player_id}.json.gz"


def fetch_player_season(key: tuple[str, str]) -> tuple[tuple[str, str], set[tuple[str, int]], str | None]:
    season, player_id = key
    path = cache_path(season, player_id)
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("ok") is True:
                return key, participation_pairs(cached.get("payload") or {}), None
        except Exception:
            pass

    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "EntityType": "Player",
        "EntityId": player_id,
    }
    errors: list[str] = []
    for attempt in range(1, 4):
        try:
            response = session().get(GAME_LOG_URL, params=params, timeout=(7, 30))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
            pairs = participation_pairs(payload)
            CACHE.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as handle:
                json.dump({"ok": True, "payload": payload}, handle, ensure_ascii=False)
            tmp.replace(path)
            return key, pairs, None
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc!r}")
            if attempt < 3:
                time.sleep(attempt)
    return key, set(), " | ".join(errors)


def apply_participation(row: dict[str, Any], evidence: dict[tuple[str, str], set[tuple[str, int]]]) -> dict[str, Any]:
    out = dict(row)
    if row.get("schedule_boundary_status") != "needs_ordering_evidence":
        return out
    season = str(row.get("season") or "")
    player_id = clean_id(row.get("player_id"))
    try:
        team_id = int(row.get("team_id") or 0)
    except Exception:
        team_id = 0
    unresolved_before = [
        str(x)
        for x in (row.get("same_day_unresolved_game_ids") or row.get("boundary_game_ids") or [])
        if str(x)
    ]
    if not season or not player_id or not team_id or not unresolved_before:
        return out

    pairs = evidence.get((season, player_id), set())
    positive = sorted({game_id for game_id in unresolved_before if (game_id, team_id) in pairs})
    if not positive:
        out["same_day_participation_resolution"] = (
            "unresolved; no positive player-game participation evidence; absence is not off-roster evidence"
        )
        return out

    remaining = sorted(set(unresolved_before) - set(positive))
    prior = [str(x) for x in (row.get("same_day_positive_participation_game_ids") or [])]
    out["same_day_positive_participation_game_ids"] = sorted(set(prior + positive))
    out["same_day_unresolved_game_ids"] = remaining
    out["team_games_min"] = int(row.get("team_games_min") or 0) + len(positive)
    out["same_day_participation_evidence_source"] = "PBP Stats player game logs; positive game+team match only"
    if not remaining:
        out["team_games_in_window"] = out["team_games_min"]
        out["team_games_max"] = out["team_games_min"]
        out["schedule_boundary_status"] = "resolved"
        out["same_day_resolution"] = "resolved_by_positive_player_game_participation"
        out["same_day_participation_resolution"] = "resolved"
        out["audit_flags"] = sorted(
            set(f for f in (row.get("audit_flags") or []) if f != "same_day_game_ordering_evidence_required")
        )
    else:
        out["same_day_participation_resolution"] = (
            "partially_resolved_by_positive_player_game_participation; remaining boundary requires roster evidence"
        )
    return out


def build() -> dict[str, Any]:
    """Resolve the cheap positive-participation subset before expensive roster evidence.

    PBP Stats game logs are queried once per unresolved player-season rather than twice per
    boundary game. Positive game+team matches prove roster presence. Non-participation and
    endpoint failure never prove roster absence; unresolved cases flow to the subsequent
    inactive/DNP roster-listing pass.
    """
    rows = read_rows(INPUT)
    unresolved = [r for r in rows if r.get("schedule_boundary_status") == "needs_ordering_evidence"]
    keys = sorted({
        (str(r.get("season") or ""), clean_id(r.get("player_id")))
        for r in unresolved
        if str(r.get("season") or "") and clean_id(r.get("player_id"))
    })

    evidence: dict[tuple[str, str], set[tuple[str, int]]] = {}
    fetch_errors: list[dict[str, Any]] = []
    started = time.monotonic()
    if keys:
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="treb-participation") as pool:
            futures = {pool.submit(fetch_player_season, key): key for key in keys}
            completed = 0
            for future in as_completed(futures):
                key = futures[future]
                try:
                    returned_key, pairs, error = future.result()
                except Exception as exc:
                    returned_key, pairs, error = key, set(), repr(exc)
                evidence[returned_key] = pairs
                if error:
                    fetch_errors.append({"season": key[0], "player_id": key[1], "error": error})
                completed += 1
                if completed % 25 == 0 or completed == len(keys):
                    elapsed = max(time.monotonic() - started, 0.001)
                    print(
                        f"player-season participation {completed}/{len(keys)} workers={WORKERS} "
                        f"fetch_errors={len(fetch_errors)} rate={completed * 60.0 / elapsed:.1f}/min",
                        flush=True,
                    )

    output_rows = [apply_participation(row, evidence) for row in rows]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(OUTPUT)

    before = len(unresolved)
    after = sum(r.get("schedule_boundary_status") == "needs_ordering_evidence" for r in output_rows)
    fully_resolved = before - after
    positive_windows = sum(bool(r.get("same_day_positive_participation_game_ids")) for r in output_rows)
    partial = sum(
        r.get("schedule_boundary_status") == "needs_ordering_evidence"
        and bool(r.get("same_day_positive_participation_game_ids"))
        for r in output_rows
    )
    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method": (
            "PBP Stats get-game-logs once per unresolved player-season; only positive matching game_id+team_id "
            "proves roster presence; absence or fetch failure is never interpreted as off-roster"
        ),
        "input_unresolved_windows": before,
        "fully_resolved_windows": fully_resolved,
        "partially_resolved_windows": partial,
        "windows_with_positive_participation": positive_windows,
        "remaining_unresolved_windows": after,
        "player_seasons_queried": len(keys),
        "workers": WORKERS,
        "fetch_error_count": len(fetch_errors),
        "fetch_errors": fetch_errors[:100],
        "output": str(OUTPUT),
        "audit": str(AUDIT),
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    SUMMARY.write_text(
        json.dumps({k: v for k, v in audit.items() if k != "fetch_errors"}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in audit.items() if k != "fetch_errors"}, indent=2), flush=True)
    return audit


def self_test() -> None:
    payload = {
        "results": [
            {"GameId": "0022300002", "TeamId": 10},
            {"GameId": "0022300003", "TeamId": 20},
        ]
    }
    pairs = participation_pairs(payload)
    assert ("0022300002", 10) in pairs
    row = {
        "season": "2023-24",
        "player_id": "123",
        "team_id": 10,
        "schedule_boundary_status": "needs_ordering_evidence",
        "same_day_unresolved_game_ids": ["0022300002"],
        "team_games_min": 4,
        "team_games_max": 5,
        "audit_flags": ["same_day_game_ordering_evidence_required"],
    }
    out = apply_participation(row, {("2023-24", "123"): pairs})
    assert out["schedule_boundary_status"] == "resolved"
    assert out["team_games_in_window"] == 5
    # A game for the wrong team must never resolve the boundary.
    wrong = apply_participation({**row, "team_id": 20}, {("2023-24", "123"): {("0022300002", 10)}})
    assert wrong["schedule_boundary_status"] == "needs_ordering_evidence"
    print("resolve_same_day_boundaries self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
