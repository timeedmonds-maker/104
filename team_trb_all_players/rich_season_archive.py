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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import hf_schema_diagnostic as core

START_YEAR = int(os.environ["PBPSTATS_START_YEAR"])
WORKERS = int(os.environ.get("PBPSTATS_WORKERS", "2"))
OUT = Path(
    os.environ.get(
        "PBPSTATS_OUTPUT_DIR",
        f"team_trb_all_players/rich_archive/{START_YEAR}",
    )
)
OUT.mkdir(parents=True, exist_ok=True)

CORE_FIELDS = [
    "season",
    "team_id",
    "team",
    "entity_id",
    "player_ids",
    "player_names",
    "seconds",
    "minutes",
    "games_played",
    "points_for",
    "points_against",
    "off_poss",
    "opp_off_poss",
    "def_poss",
    "opp_def_poss",
    "total_poss",
    "plus_minus",
    "off_rating",
    "def_rating",
    "net_rating",
    "pace",
    "team_rebounds",
    "opponent_rebounds",
    "team_off_rebounds",
    "team_def_rebounds",
    "opponent_off_rebounds",
    "opponent_def_rebounds",
    "team_fg2m",
    "team_fg2a",
    "team_fg3m",
    "team_fg3a",
    "team_fta",
    "opponent_fg2m",
    "opponent_fg2a",
    "opponent_fg3m",
    "opponent_fg3a",
    "opponent_fta",
    "team_turnovers",
    "opponent_turnovers",
    "team_assists",
    "opponent_assists",
    "team_steals",
    "opponent_steals",
    "team_blocks",
    "opponent_blocks",
    "team_fouls",
    "opponent_fouls",
    "team_second_chance_points",
    "opponent_second_chance_points",
]


def numeric(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value in (None, ""):
        return default
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Non-finite {key}: {value!r}")
    return result


def safe_rate(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return 100.0 * numerator / denominator


def request_payload(
    season: str,
    team_id: int,
    entity_type: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "Type": entity_type,
        "TeamId": str(team_id),
    }
    errors: list[str] = []
    for attempt in range(6):
        started = time.monotonic()
        try:
            response = core.get_session().get(core.API_URL, params=params, timeout=150)
            elapsed = time.monotonic() - started
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("multi_row_table_data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("No multi_row_table_data list")
            request_meta = {
                "season": season,
                "team_id": team_id,
                "entity_type": entity_type,
                "request_url": response.url,
                "status": response.status_code,
                "response_bytes": len(response.content),
                "elapsed_seconds": round(elapsed, 3),
                "attempts": attempt + 1,
                "row_count": len(rows),
            }
            return payload, request_meta
        except Exception as exc:
            errors.append(f"attempt {attempt + 1}: {exc!r}")
            if attempt < 5:
                time.sleep(min(2**attempt, 20) + random.random())
    raise RuntimeError("; ".join(errors))


@dataclass
class ArchiveResult:
    season: str
    team_id: int
    team: str
    paired_rows: list[dict[str, Any]]
    core_rows: list[dict[str, Any]]
    single_row_payloads: dict[str, Any]
    request_meta: list[dict[str, Any]]


def build_team_archive(season: str, team_id: int) -> ArchiveResult:
    lineup_payload, lineup_meta = request_payload(season, team_id, "Lineup")
    opponent_payload, opponent_meta = request_payload(season, team_id, "LineupOpponent")
    lineups = lineup_payload["multi_row_table_data"]
    opponents = opponent_payload["multi_row_table_data"]

    if not lineups and not opponents:
        return ArchiveResult(
            season,
            team_id,
            "",
            [],
            [],
            {
                "Lineup": lineup_payload.get("single_row_table_data"),
                "LineupOpponent": opponent_payload.get("single_row_table_data"),
            },
            [lineup_meta, opponent_meta],
        )
    if not lineups or not opponents:
        raise ValueError(
            f"One-sided response: lineups={len(lineups)}, opponents={len(opponents)}"
        )
    if len(lineups) >= core.MAX_ROWS or len(opponents) >= core.MAX_ROWS:
        raise ValueError(
            f"Possible row truncation: lineups={len(lineups)}, opponents={len(opponents)}"
        )

    lineup_by_id = {str(row.get("EntityId")): row for row in lineups}
    opponent_by_id = {str(row.get("EntityId")): row for row in opponents}
    if len(lineup_by_id) != len(lineups) or len(opponent_by_id) != len(opponents):
        raise ValueError("Duplicate EntityId")
    if lineup_by_id.keys() != opponent_by_id.keys():
        raise ValueError("Lineup and opponent EntityId sets do not match")

    team = str(lineups[0].get("TeamAbbreviation") or "")
    paired_rows: list[dict[str, Any]] = []
    core_rows: list[dict[str, Any]] = []

    for entity_id in sorted(lineup_by_id):
        row = lineup_by_id[entity_id]
        opp = opponent_by_id[entity_id]
        player_ids = entity_id.split("-")
        player_names = [x.strip() for x in str(row.get("Name") or "").split(",")]
        if len(player_ids) != 5 or len(player_names) != 5:
            raise ValueError(f"Invalid five-player lineup {entity_id}: {row.get('Name')!r}")

        seconds = numeric(row, "SecondsPlayed", numeric(row, "Minutes") * 60.0)
        opp_seconds = numeric(opp, "SecondsPlayed", seconds)
        if seconds < 0 or abs(seconds - opp_seconds) > 1.0:
            raise ValueError(
                f"Seconds mismatch for {entity_id}: team={seconds}, opponent={opp_seconds}"
            )

        points_for = numeric(row, "Points")
        points_against = numeric(opp, "Points")
        off_poss = numeric(row, "OffPoss")
        opp_off_poss = numeric(opp, "OffPoss")
        off_rating = safe_rate(points_for, off_poss)
        def_rating = safe_rate(points_against, opp_off_poss)

        paired_rows.append(
            {
                "season": season,
                "team_id": team_id,
                "team": team,
                "entity_id": entity_id,
                "player_ids": player_ids,
                "player_names": player_names,
                "lineup": row,
                "lineup_opponent": opp,
            }
        )
        core_rows.append(
            {
                "season": season,
                "team_id": team_id,
                "team": team,
                "entity_id": entity_id,
                "player_ids": "-".join(player_ids),
                "player_names": " | ".join(player_names),
                "seconds": round(seconds, 3),
                "minutes": round(seconds / 60.0, 6),
                "games_played": row.get("GamesPlayed", ""),
                "points_for": points_for,
                "points_against": points_against,
                "off_poss": off_poss,
                "opp_off_poss": opp_off_poss,
                "def_poss": numeric(row, "DefPoss"),
                "opp_def_poss": numeric(opp, "DefPoss"),
                "total_poss": numeric(row, "TotalPoss"),
                "plus_minus": numeric(row, "PlusMinus"),
                "off_rating": round(off_rating, 8) if off_rating is not None else "",
                "def_rating": round(def_rating, 8) if def_rating is not None else "",
                "net_rating": (
                    round(off_rating - def_rating, 8)
                    if off_rating is not None and def_rating is not None
                    else ""
                ),
                "pace": row.get("Pace", ""),
                "team_rebounds": numeric(row, "Rebounds"),
                "opponent_rebounds": numeric(opp, "Rebounds"),
                "team_off_rebounds": numeric(row, "OffRebounds"),
                "team_def_rebounds": numeric(row, "DefRebounds"),
                "opponent_off_rebounds": numeric(opp, "OffRebounds"),
                "opponent_def_rebounds": numeric(opp, "DefRebounds"),
                "team_fg2m": numeric(row, "FG2M"),
                "team_fg2a": numeric(row, "FG2A"),
                "team_fg3m": numeric(row, "FG3M"),
                "team_fg3a": numeric(row, "FG3A"),
                "team_fta": numeric(row, "FTA"),
                "opponent_fg2m": numeric(opp, "FG2M"),
                "opponent_fg2a": numeric(opp, "FG2A"),
                "opponent_fg3m": numeric(opp, "FG3M"),
                "opponent_fg3a": numeric(opp, "FG3A"),
                "opponent_fta": numeric(opp, "FTA"),
                "team_turnovers": numeric(row, "Turnovers"),
                "opponent_turnovers": numeric(opp, "Turnovers"),
                "team_assists": numeric(row, "Assists"),
                "opponent_assists": numeric(opp, "Assists"),
                "team_steals": numeric(row, "Steals"),
                "opponent_steals": numeric(opp, "Steals"),
                "team_blocks": numeric(row, "Blocks"),
                "opponent_blocks": numeric(opp, "Blocks"),
                "team_fouls": numeric(row, "Fouls"),
                "opponent_fouls": numeric(opp, "Fouls"),
                "team_second_chance_points": numeric(row, "SecondChancePoints"),
                "opponent_second_chance_points": numeric(opp, "SecondChancePoints"),
            }
        )

    return ArchiveResult(
        season,
        team_id,
        team,
        paired_rows,
        core_rows,
        {
            "Lineup": lineup_payload.get("single_row_table_data"),
            "LineupOpponent": opponent_payload.get("single_row_table_data"),
        },
        [lineup_meta, opponent_meta],
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    season = core.season_label(START_YEAR)
    results: list[ArchiveResult] = []
    failures: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_map = {
            executor.submit(build_team_archive, season, team_id): team_id
            for team_id in core.TEAM_IDS
        }
        for completed, future in enumerate(as_completed(future_map), start=1):
            team_id = future_map[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    f"[{completed}/30] OK {season} {team_id} "
                    f"{result.team or 'inactive'} rows={len(result.paired_rows)}",
                    flush=True,
                )
            except Exception as exc:
                failure = {"season": season, "team_id": team_id, "error": repr(exc)}
                failures.append(failure)
                print(f"[{completed}/30] FAIL {failure}", flush=True)

    results.sort(key=lambda item: item.team_id)
    paired = [row for result in results for row in result.paired_rows]
    paired.sort(key=lambda row: (row["team_id"], row["entity_id"]))
    core_rows = [row for result in results for row in result.core_rows]
    core_rows.sort(key=lambda row: (row["team_id"], row["entity_id"]))
    request_rows = [row for result in results for row in result.request_meta]
    request_rows.sort(key=lambda row: (row["team_id"], row["entity_type"]))

    archive_path = OUT / "paired_lineups_full_fields.jsonl.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
        for row in paired:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    summary_payloads = {
        str(result.team_id): {
            "season": result.season,
            "team": result.team,
            **result.single_row_payloads,
        }
        for result in results
    }
    summary_path = OUT / "team_single_row_payloads.json.gz"
    with gzip.open(summary_path, "wt", encoding="utf-8") as handle:
        json.dump(summary_payloads, handle, ensure_ascii=False, separators=(",", ":"))

    write_csv(OUT / "lineup_core_metrics.csv", core_rows, CORE_FIELDS)
    write_csv(
        OUT / "request_audit.csv",
        request_rows,
        [
            "season",
            "team_id",
            "entity_type",
            "request_url",
            "status",
            "response_bytes",
            "elapsed_seconds",
            "attempts",
            "row_count",
        ],
    )
    if failures:
        write_csv(OUT / "request_failures.csv", failures, ["season", "team_id", "error"])

    team_field_counts: Counter[str] = Counter()
    opponent_field_counts: Counter[str] = Counter()
    for row in paired:
        team_field_counts.update(row["lineup"].keys())
        opponent_field_counts.update(row["lineup_opponent"].keys())

    schema = {
        "season": season,
        "paired_lineup_rows": len(paired),
        "lineup_fields": sorted(team_field_counts),
        "lineup_opponent_fields": sorted(opponent_field_counts),
        "lineup_field_coverage": dict(sorted(team_field_counts.items())),
        "lineup_opponent_field_coverage": dict(sorted(opponent_field_counts.items())),
        "lineup_field_count": len(team_field_counts),
        "lineup_opponent_field_count": len(opponent_field_counts),
    }
    (OUT / "schema_manifest.json").write_text(
        json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    expected_active = 29 if START_YEAR <= 2003 else 30
    active = sum(bool(result.paired_rows) for result in results)
    validation_errors: list[str] = []
    if failures:
        validation_errors.append(f"{len(failures)} failed team archives")
    if len(results) != 30:
        validation_errors.append(f"only {len(results)} of 30 franchise IDs returned")
    if active != expected_active:
        validation_errors.append(f"active teams={active}, expected={expected_active}")
    if len(paired) < 3_000:
        validation_errors.append(f"too few paired lineup rows: {len(paired)}")
    if len(team_field_counts) < 150 or len(opponent_field_counts) < 150:
        validation_errors.append(
            f"unexpectedly narrow schema: team={len(team_field_counts)}, "
            f"opponent={len(opponent_field_counts)}"
        )

    metadata = {
        "season": season,
        "start_year": START_YEAR,
        "workers": WORKERS,
        "active_teams": active,
        "paired_lineup_rows": len(paired),
        "lineup_field_count": len(team_field_counts),
        "lineup_opponent_field_count": len(opponent_field_counts),
        "raw_archive_file": archive_path.name,
        "raw_archive_bytes": archive_path.stat().st_size,
        "raw_archive_sha256": file_sha256(archive_path),
        "team_summary_file": summary_path.name,
        "team_summary_bytes": summary_path.stat().st_size,
        "team_summary_sha256": file_sha256(summary_path),
        "complete": not validation_errors,
        "validation_errors": validation_errors,
        "method": (
            "Preserve every field returned by paired PBP Stats Type=Lineup and "
            "Type=LineupOpponent rows, keyed by the same five-player EntityId. "
            "The compact CSV contains commonly used additive fields and derived "
            "offensive, defensive and net ratings; the compressed JSONL remains "
            "the immutable source for future derived metrics."
        ),
        "source": core.API_URL,
    }
    (OUT / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "validation.json").write_text(
        json.dumps(
            {
                "season": season,
                "complete": not validation_errors,
                "errors": validation_errors,
                "metadata": metadata,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(json.dumps(metadata, indent=2), flush=True)
    if validation_errors:
        raise SystemExit("; ".join(validation_errors))

    (OUT / "season_complete.json").write_text(
        json.dumps(
            {
                "season": season,
                "validated": True,
                "active_teams": active,
                "paired_lineup_rows": len(paired),
                "raw_archive_sha256": metadata["raw_archive_sha256"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
