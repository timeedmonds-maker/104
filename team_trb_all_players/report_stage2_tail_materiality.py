from __future__ import annotations

import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB = BASE / "impact_database"
CORE = DB / "outputs"
SRC = DB / "corrected_off"
QUEUE = SRC / "deferred_failure_queue.json"
OUT = SRC / "tail_materiality_report.json"

PLAYER_ID_FIELDS = ("subject_player_id", "player_id", "PLAYER_ID", "PlayerId")
PLAYER_NAME_FIELDS = ("subject_player", "player", "PLAYER_NAME", "PlayerName")
TEAM_ID_FIELDS = ("team_id", "TEAM_ID", "TeamId")
MINUTE_FIELDS = ("minutes_on", "on_minutes", "MIN", "minutes", "Minutes")


def pick(fields: list[str] | tuple[str, ...], names: tuple[str, ...]) -> str | None:
    fs = set(fields)
    return next((n for n in names if n in fs), None)


def iso_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def duration_days(row: dict[str, Any]) -> int | None:
    a = iso_day(row.get("query_start_date")); b = iso_day(row.get("query_end_date"))
    if a is None or b is None or b < a:
        return None
    return (b - a).days + 1


def queue_rows() -> list[dict[str, Any]]:
    raw = json.loads(QUEUE.read_text(encoding="utf-8"))
    windows = raw.get("windows") if isinstance(raw, dict) else None
    if not isinstance(windows, dict):
        raise RuntimeError("deferred queue has no windows object")
    return [v for v in windows.values() if isinstance(v, dict)]


def candidate_core_files() -> list[Path]:
    # Inspect headers only, then use every season file that exposes a player id and an ON-minute field.
    out: list[Path] = []
    for path in sorted(CORE.rglob("*.csv.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as h:
                reader = csv.reader(h)
                header = next(reader, [])
        except Exception:
            continue
        if pick(header, PLAYER_ID_FIELDS) and pick(header, MINUTE_FIELDS):
            out.append(path)
    return out


def career_minutes() -> tuple[dict[str, float], dict[str, str], list[str]]:
    # Deduplicate by season/team/player because metric-long core files repeat the same minutes across metrics.
    stint_minutes: dict[tuple[str, str, str], float] = {}
    names: dict[str, str] = {}
    used: list[str] = []
    for path in candidate_core_files():
        season = path.parent.name if len(path.parent.name) == 7 and path.parent.name[4] == "-" else ""
        try:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as h:
                reader = csv.DictReader(h)
                fields = reader.fieldnames or []
                pidf = pick(fields, PLAYER_ID_FIELDS); minf = pick(fields, MINUTE_FIELDS)
                namef = pick(fields, PLAYER_NAME_FIELDS); teamf = pick(fields, TEAM_ID_FIELDS)
                if not pidf or not minf:
                    continue
                rows_seen = 0
                useful = False
                for row in reader:
                    rows_seen += 1
                    pid = str(row.get(pidf) or "").replace(".0", "").strip()
                    if not pid:
                        continue
                    try:
                        mins = float(row.get(minf) or 0)
                    except Exception:
                        continue
                    if mins < 0:
                        continue
                    team = str(row.get(teamf) or "") if teamf else ""
                    key = (season, team, pid)
                    if mins > stint_minutes.get(key, -1.0):
                        stint_minutes[key] = mins
                    if namef and row.get(namef):
                        names[pid] = str(row.get(namef))
                    useful = True
                if useful:
                    used.append(str(path.relative_to(BASE)))
        except Exception:
            continue
    career: dict[str, float] = defaultdict(float)
    for (_, _, pid), mins in stint_minutes.items():
        career[pid] += mins
    return dict(career), names, used


def build() -> dict[str, Any]:
    rows = queue_rows()
    durations = [d for d in (duration_days(r) for r in rows) if d is not None]
    players = {str(r.get("player_id") or ""): str(r.get("player_name") or "") for r in rows if r.get("player_id")}
    career, core_names, used_files = career_minutes()
    qualifying = []
    near = []
    for pid, qname in players.items():
        mins = career.get(pid)
        item = {"player_id": pid, "player_name": qname or core_names.get(pid), "core_career_minutes": round(mins, 3) if mins is not None else None}
        if mins is not None and mins >= 10000:
            qualifying.append(item)
        elif mins is not None and mins >= 9000:
            near.append(item)
    qualifying.sort(key=lambda x: x["core_career_minutes"] or 0, reverse=True)
    near.sort(key=lambda x: x["core_career_minutes"] or 0, reverse=True)

    by_season = Counter(str(r.get("season") or "") for r in rows)
    failures = Counter(str(r.get("failure_class") or "unknown") for r in rows)
    long_rows = []
    for r in rows:
        d = duration_days(r)
        if d is not None and d >= 30:
            long_rows.append({
                "season": r.get("season"), "player_id": str(r.get("player_id") or ""), "player_name": r.get("player_name"),
                "start": r.get("query_start_date"), "end": r.get("query_end_date"), "duration_days": d,
                "core_career_minutes": round(career.get(str(r.get("player_id") or ""), 0.0), 3) if str(r.get("player_id") or "") in career else None,
            })
    long_rows.sort(key=lambda x: x["duration_days"], reverse=True)

    def count_le(n: int) -> int:
        return sum(1 for d in durations if d <= n)

    total = len(rows)
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "unresolved_windows": total,
        "unique_unresolved_players": len(players),
        "window_share_of_15427_pct": round(100 * total / 15427, 4),
        "duration_days": {
            "known": len(durations),
            "min": min(durations) if durations else None,
            "median": sorted(durations)[len(durations)//2] if durations else None,
            "max": max(durations) if durations else None,
            "one_day": sum(d == 1 for d in durations),
            "le_3_days": count_le(3),
            "le_7_days": count_le(7),
            "le_14_days": count_le(14),
            "le_30_days": count_le(30),
            "gt_30_days": sum(d > 30 for d in durations),
            "sum_calendar_days_not_player_minutes": sum(durations),
        },
        "failure_classes": dict(failures.most_common()),
        "season_distribution": dict(sorted(by_season.items())),
        "core_minutes_scan": {
            "files_used": used_files,
            "players_with_core_career_minutes": len(career),
            "unresolved_players_at_or_above_10000_core_minutes": len(qualifying),
            "qualifying_players": qualifying,
            "unresolved_players_9000_to_9999_core_minutes": len(near),
            "near_threshold_players": near,
        },
        "long_windows_30plus_days": long_rows,
        "interpretation": {
            "important_scope_note": "These unresolved records are tenure-correction windows. The original 780/780 core ON database remains complete; unresolved windows principally block exact tenure-corrected OFF/swing assembly for the affected player-team-season segments.",
            "materiality_gate": "Low materiality is supported if unresolved windows are overwhelmingly short and no/very few unresolved players belong to the >=10000-minute comparison population. Do not infer low materiality from raw window count alone.",
        },
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    build()
