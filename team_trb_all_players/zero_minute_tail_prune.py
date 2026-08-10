from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import run_corrected_off_batch_v4 as v4

v3 = v4.v3
batch = v4.batch
core = v4.core

AUDIT = core.OUT / "zero_minute_tail_exclusions.json"
QUEUE = core.OUT / "deferred_failure_queue.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_audit() -> dict[str, Any]:
    if not AUDIT.exists():
        return {"generated_utc": now(), "policy": "Exclude only unresolved tenure windows independently verified to contain zero ON-court minutes.", "windows": {}}
    try:
        data = json.loads(AUDIT.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("windows"), dict):
            return data
    except Exception:
        pass
    return {"generated_utc": now(), "policy": "Exclude only unresolved tenure windows independently verified to contain zero ON-court minutes.", "windows": {}}


def save_audit(data: dict[str, Any]) -> None:
    data["generated_utc"] = now()
    data["excluded_window_count"] = len(data.get("windows") or {})
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(data, indent=2), encoding="utf-8")


def install_filter() -> set[str]:
    audit = load_audit()
    excluded = set((audit.get("windows") or {}).keys())
    original = batch.impact_windows
    if getattr(original, "_zero_tail_filtered", False):
        return excluded

    def filtered():
        return [w for w in original() if v4.window_key(w) not in excluded]

    filtered._zero_tail_filtered = True  # type: ignore[attr-defined]
    batch.impact_windows = filtered
    return excluded


def prune(request_fn: Callable[[str, dict[str, str]], tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    audit = load_audit()
    excluded = set((audit.get("windows") or {}).keys())

    # Use the unfiltered Stage-1 workset by temporarily reading through the current function.
    windows = batch.impact_windows()
    by_key = {v4.window_key(w): w for w in windows}
    state = v4.load_queue()

    checked = 0
    newly_excluded = 0
    positive_minutes = 0
    indeterminate = 0

    # Group identical team/date intervals so the player-independent stat payload is fetched once.
    groups: dict[tuple[str, int, str, str], list[tuple[str, dict[str, Any]]]] = {}
    for key in list(state):
        if key in excluded:
            state.pop(key, None)
            continue
        w = by_key.get(key)
        if not w:
            continue
        start, end = core.query_dates(w)
        gkey = (str(w.get("season") or ""), int(w.get("team_id") or 0), start, end)
        groups.setdefault(gkey, []).append((key, w))

    for (season, team_id, start, end), items in groups.items():
        payload, meta = request_fn(core.STAT_URL, {
            "Season": season,
            "SeasonType": "Regular Season",
            "TeamId": str(team_id),
            "Stat": "OffRebounds",
            "FromDate": start,
            "ToDate": end,
        })
        result_rows = core.rows(payload)
        if not meta.get("ok") or not result_rows:
            indeterminate += len(items)
            continue

        for key, w in items:
            checked += 1
            player_id = core.clean_id(w.get("player_id"))
            player_name = str(w.get("player_name") or w.get("player") or "").strip()
            minutes_on, _minutes_off, row = v3.robust_stat_minutes(payload, player_id, player_name)

            # A successful non-empty team/date stat payload contains players who appeared.
            # Exact 0.0 or absence of the rostered player both establish no ON-court minutes.
            zero = (minutes_on is not None and abs(minutes_on) < 1e-9) or row is None
            if zero:
                audit.setdefault("windows", {})[key] = {
                    "season": season,
                    "team_id": team_id,
                    "player_id": player_id,
                    "player_name": player_name,
                    "query_start_date": start,
                    "query_end_date": end,
                    "team_games_in_window": w.get("team_games_in_window"),
                    "verified_minutes_on": 0.0,
                    "verification": "successful non-empty PBP Stats team/date stat payload; player absent or MinutesOn=0",
                    "verified_utc": now(),
                }
                excluded.add(key)
                state.pop(key, None)
                newly_excluded += 1
            else:
                positive_minutes += 1

    v4.save_queue(state)
    save_audit(audit)

    # Install a fresh denominator filter after exclusions were updated.
    original = batch.impact_windows
    if getattr(original, "_zero_tail_filtered", False):
        # Existing closure may hold an older set; reconstruct from Stage-1 source directly.
        def filtered_latest():
            raw = core.load_rows(core.WINDOWS)
            impact = [w for w in raw if w.get("schedule_boundary_status") == "resolved" and core.clean_id(w.get("player_id")) and not bool(w.get("zero_minute_only")) and not bool(w.get("zero_game_window"))]
            return [w for w in impact if v4.window_key(w) not in excluded]
        filtered_latest._zero_tail_filtered = True  # type: ignore[attr-defined]
        batch.impact_windows = filtered_latest
    else:
        base = original
        def filtered_latest():
            return [w for w in base() if v4.window_key(w) not in excluded]
        filtered_latest._zero_tail_filtered = True  # type: ignore[attr-defined]
        batch.impact_windows = filtered_latest

    report = {
        "generated_utc": now(),
        "queue_windows_before_scan": len(state) + newly_excluded,
        "groups_checked": len(groups),
        "player_windows_checked_with_successful_stat_payload": checked,
        "newly_excluded_zero_minute_windows": newly_excluded,
        "total_excluded_zero_minute_windows": len(excluded),
        "verified_positive_minute_windows": positive_minutes,
        "indeterminate_windows": indeterminate,
        "remaining_retry_queue_windows": len(state),
    }
    (core.OUT / "zero_minute_tail_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
