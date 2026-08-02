from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import full_adaptive_batch as batch

ORIGINAL_ADAPTIVE = batch.adaptive


def archive_path(team_id: int, start: date, end: date) -> Path:
    return batch.OUT / "raw_windows" / str(team_id) / f"{start}_{end}.json.gz"


def load_cached_archive(
    team_id: int,
    start: date,
    end: date,
    depth: int,
) -> tuple[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], bool] | None:
    path = archive_path(team_id, start, end)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("cached payload is not an object")
        if str(payload.get("season")) != batch.SEASON:
            raise ValueError("cached season mismatch")
        if int(payload.get("team_id")) != team_id:
            raise ValueError("cached team mismatch")
        if str(payload.get("from_date")) != start.isoformat():
            raise ValueError("cached start-date mismatch")
        if str(payload.get("to_date")) != end.isoformat():
            raise ValueError("cached end-date mismatch")
        lineups = payload.get("lineup_rows")
        opponents = payload.get("lineup_opponent_rows")
        if not isinstance(lineups, list) or not isinstance(opponents, list):
            raise ValueError("cached rows are not lists")
        batch.validate_pair(lineups, opponents)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        batch.MANIFEST.append({
            "season": batch.SEASON,
            "group": batch.GROUP,
            "team_id": team_id,
            "from_date": start,
            "to_date": end,
            "days": (end - start).days + 1,
            "depth": depth,
            "status": "success",
            "lineup_rows": len(lineups),
            "opponent_rows": len(opponents),
            "archive_path": str(path.relative_to(batch.OUT)),
            "archive_sha256": digest,
            "reason": "reused cached archive",
        })
        print(
            f"REUSE team={team_id} {start}..{end} rows={len(lineups)}",
            flush=True,
        )
        return [(lineups, opponents)], True
    except Exception as exc:
        print(
            f"DISCARD CACHE team={team_id} {start}..{end}: {exc!r}",
            flush=True,
        )
        path.unlink(missing_ok=True)
        return None


def cached_adaptive(
    team_id: int,
    start: date,
    end: date,
    depth: int = 0,
) -> tuple[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]], bool]:
    cached = load_cached_archive(team_id, start, end, depth)
    if cached is not None:
        return cached
    return ORIGINAL_ADAPTIVE(team_id, start, end, depth)


def main() -> int:
    # ORIGINAL_ADAPTIVE resolves recursive calls through the module-global
    # `adaptive` name. Replacing it here makes every recursive child cache-aware.
    batch.adaptive = cached_adaptive
    return batch.main()


if __name__ == "__main__":
    sys.exit(main())
