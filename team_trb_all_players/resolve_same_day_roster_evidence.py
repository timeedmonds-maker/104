from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
WINDOWS = ROOT / "player_team_season_windows_evidence_audited.jsonl.gz"
AUDIT = ROOT / "same_day_roster_evidence_audit.json"
SUMMARY = ROOT / "same_day_roster_evidence_summary.json"
CACHE = ROOT / "boundary_game_roster_cache"

SUMMARY_URL = "https://stats.nba.com/stats/boxscoresummaryv2"
TRADITIONAL_URL = "https://stats.nba.com/stats/boxscoretraditionalv2"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
}


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"missing required input: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def result_rows(payload: dict[str, Any], target_name: str) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(result_sets, dict):
        result_sets = [result_sets]
    for result_set in result_sets:
        if not isinstance(result_set, dict):
            continue
        if str(result_set.get("name") or "").casefold() != target_name.casefold():
            continue
        headers = [str(x) for x in result_set.get("headers") or []]
        return [dict(zip(headers, row)) for row in result_set.get("rowSet") or []]
    return []


def clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if text.isdigit() and text != "0" else ""


def clean_team_id(value: Any) -> int | None:
    try:
        team_id = int(str(value).strip())
    except Exception:
        return None
    return team_id if team_id > 0 else None


def roster_pairs_from_payloads(summary_payload: dict[str, Any], traditional_payload: dict[str, Any]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    evidence: dict[tuple[str, int], list[dict[str, Any]]] = {}

    for row in result_rows(summary_payload, "InactivePlayers"):
        player_id = clean_id(row.get("PLAYER_ID") or row.get("Player_ID"))
        team_id = clean_team_id(row.get("TEAM_ID") or row.get("Team_ID"))
        if player_id and team_id:
            evidence.setdefault((player_id, team_id), []).append({
                "endpoint": "boxscoresummaryv2",
                "result_set": "InactivePlayers",
                "player_name": row.get("PLAYER_NAME") or row.get("Player_Name"),
                "roster_evidence_type": "listed_inactive",
            })

    for row in result_rows(traditional_payload, "PlayerStats"):
        player_id = clean_id(row.get("PLAYER_ID") or row.get("Player_ID"))
        team_id = clean_team_id(row.get("TEAM_ID") or row.get("Team_ID"))
        if player_id and team_id:
            evidence.setdefault((player_id, team_id), []).append({
                "endpoint": "boxscoretraditionalv2",
                "result_set": "PlayerStats",
                "player_name": row.get("PLAYER_NAME") or row.get("Player_Name"),
                "minutes": row.get("MIN"),
                "roster_evidence_type": "listed_in_box_score",
            })
    return evidence


def request_json(url: str, params: dict[str, str], attempts: int = 3) -> tuple[dict[str, Any], str | None]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=(8, 25))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
            return payload, None
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    return {}, " | ".join(errors)


def fetch_game_roster(game_id: str) -> tuple[dict[tuple[str, int], list[dict[str, Any]]], list[str]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / f"{game_id}.json.gz"
    if cache_path.exists():
        try:
            with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
                cached = json.load(handle)
            return roster_pairs_from_payloads(cached.get("summary") or {}, cached.get("traditional") or {}), cached.get("errors") or []
        except Exception:
            pass

    errors: list[str] = []
    summary_payload, summary_error = request_json(SUMMARY_URL, {"GameID": game_id})
    if summary_error:
        errors.append(f"boxscoresummaryv2: {summary_error}")

    traditional_payload, traditional_error = request_json(TRADITIONAL_URL, {
        "GameID": game_id,
        "StartPeriod": "1",
        "EndPeriod": "10",
        "StartRange": "0",
        "EndRange": "0",
        "RangeType": "0",
    })
    if traditional_error:
        errors.append(f"boxscoretraditionalv2: {traditional_error}")

    cached = {
        "game_id": game_id,
        "summary": summary_payload,
        "traditional": traditional_payload,
        "errors": errors,
    }
    with gzip.open(cache_path, "wt", encoding="utf-8") as handle:
        json.dump(cached, handle, ensure_ascii=False)
    time.sleep(0.45)
    return roster_pairs_from_payloads(summary_payload, traditional_payload), errors


def apply_roster_evidence(row: dict[str, Any], by_game: dict[str, dict[tuple[str, int], list[dict[str, Any]]]]) -> dict[str, Any]:
    out = dict(row)
    if row.get("schedule_boundary_status") != "needs_ordering_evidence":
        return out

    player_id = str(row.get("player_id") or "")
    team_id = int(row.get("team_id") or 0)
    unresolved_before = [str(x) for x in (row.get("same_day_unresolved_game_ids") or row.get("boundary_game_ids") or [])]
    if not player_id or not team_id or not unresolved_before:
        return out

    newly_resolved: list[str] = []
    details: list[dict[str, Any]] = []
    for game_id in unresolved_before:
        evidence = by_game.get(game_id, {}).get((player_id, team_id), [])
        if evidence:
            newly_resolved.append(game_id)
            details.append({"game_id": game_id, "evidence": evidence})

    if not newly_resolved:
        out["same_day_roster_resolution"] = "unresolved; no positive roster-listing evidence; absence is not off-roster evidence"
        return out

    remaining = sorted(set(unresolved_before) - set(newly_resolved))
    prior_positive = sorted(set(str(x) for x in (row.get("same_day_positive_participation_game_ids") or [])))
    out["same_day_positive_roster_game_ids"] = sorted(set(newly_resolved))
    out["same_day_roster_evidence"] = details
    out["same_day_unresolved_game_ids"] = remaining

    new_min = int(row.get("team_games_min") or 0) + len(newly_resolved)
    out["team_games_min"] = new_min
    if not remaining:
        out["team_games_in_window"] = new_min
        out["team_games_max"] = new_min
        out["schedule_boundary_status"] = "resolved"
        out["same_day_resolution"] = "resolved_by_positive_roster_listing_evidence"
        out["same_day_roster_resolution"] = "resolved"
        flags = [f for f in (row.get("audit_flags") or []) if f != "same_day_game_ordering_evidence_required"]
        out["audit_flags"] = sorted(set(flags))
    else:
        out["same_day_roster_resolution"] = "partially_resolved_by_positive_roster_listing_evidence; remaining boundary requires ordering evidence"

    out["same_day_all_positive_game_ids"] = sorted(set(prior_positive + newly_resolved))
    return out


def build() -> dict[str, Any]:
    rows = read_rows(WINDOWS)
    unresolved_rows = [row for row in rows if row.get("schedule_boundary_status") == "needs_ordering_evidence"]
    game_ids = sorted({
        str(game_id)
        for row in unresolved_rows
        for game_id in (row.get("same_day_unresolved_game_ids") or row.get("boundary_game_ids") or [])
        if str(game_id)
    })

    by_game: dict[str, dict[tuple[str, int], list[dict[str, Any]]]] = {}
    fetch_errors: list[dict[str, Any]] = []
    for i, game_id in enumerate(game_ids, 1):
        evidence, errors = fetch_game_roster(game_id)
        by_game[game_id] = evidence
        if errors:
            fetch_errors.append({"game_id": game_id, "errors": errors})
        if i % 25 == 0 or i == len(game_ids):
            print(f"boundary-game roster evidence {i}/{len(game_ids)}; fetch_errors={len(fetch_errors)}")

    output_rows = [apply_roster_evidence(row, by_game) for row in rows]
    tmp = WINDOWS.with_suffix(WINDOWS.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(WINDOWS)

    before = len(unresolved_rows)
    after = sum(row.get("schedule_boundary_status") == "needs_ordering_evidence" for row in output_rows)
    fully_resolved = before - after
    positive_roster_windows = sum(bool(row.get("same_day_positive_roster_game_ids")) for row in output_rows)
    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method": (
            "positive roster-listing evidence from NBA box-score InactivePlayers and PlayerStats only; "
            "absence from either endpoint is never interpreted as off-roster"
        ),
        "input_unresolved_windows": before,
        "boundary_games_queried": len(game_ids),
        "windows_with_positive_roster_evidence": positive_roster_windows,
        "fully_resolved_windows": fully_resolved,
        "remaining_unresolved_windows": after,
        "fetch_error_count": len(fetch_errors),
        "fetch_errors": fetch_errors,
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {k: v for k, v in audit.items() if k != "fetch_errors"}
    summary["audit"] = str(AUDIT)
    summary["output"] = str(WINDOWS)
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def self_test() -> None:
    summary_payload = {
        "resultSets": [{
            "name": "InactivePlayers",
            "headers": ["PLAYER_ID", "TEAM_ID", "PLAYER_NAME"],
            "rowSet": [["123", 10, "Inactive Player"]],
        }]
    }
    traditional_payload = {
        "resultSets": [{
            "name": "PlayerStats",
            "headers": ["PLAYER_ID", "TEAM_ID", "PLAYER_NAME", "MIN"],
            "rowSet": [["456", 20, "DNP Player", None]],
        }]
    }
    pairs = roster_pairs_from_payloads(summary_payload, traditional_payload)
    assert ("123", 10) in pairs
    assert ("456", 20) in pairs

    row = {
        "season": "2023-24", "player_id": "123", "team_id": 10,
        "schedule_boundary_status": "needs_ordering_evidence",
        "boundary_game_ids": ["0022300001"],
        "same_day_unresolved_game_ids": ["0022300001"],
        "team_games_min": 4, "team_games_max": 5,
        "audit_flags": ["same_day_game_ordering_evidence_required"],
    }
    out = apply_roster_evidence(row, {"0022300001": pairs})
    assert out["schedule_boundary_status"] == "resolved"
    assert out["team_games_in_window"] == 5
    assert out["same_day_positive_roster_game_ids"] == ["0022300001"]

    absent = apply_roster_evidence({**row, "player_id": "999"}, {"0022300001": pairs})
    assert absent["schedule_boundary_status"] == "needs_ordering_evidence"
    assert absent["team_games_min"] == 4
    print("resolve_same_day_roster_evidence self-test PASSED")


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
