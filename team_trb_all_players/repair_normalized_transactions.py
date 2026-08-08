from __future__ import annotations

import gzip
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
NORMALIZED = ROOT / "normalized_transactions.jsonl.gz"
UNRESOLVED = ROOT / "normalization_unresolved.json"
SUMMARY = ROOT / "normalization_repair_summary.json"

PASSIVE_CLAIM_RE = re.compile(
    rf"claimed\s+on\s+waivers\s+by\s+the\s+(?P<dst>{norm.TEAM_PATTERN})(?:\s+from\s+the\s+(?P<src>{norm.TEAM_PATTERN}))?",
    re.IGNORECASE,
)
PASSIVE_WAIVE_RE = re.compile(rf"(?:was\s+)?waived\s+by\s+the\s+(?P<src>{norm.TEAM_PATTERN})", re.IGNORECASE)
PASSIVE_RELEASE_RE = re.compile(rf"(?:was\s+)?released\s+by\s+the\s+(?P<src>{norm.TEAM_PATTERN})", re.IGNORECASE)
SIGNED_WITH_RE = re.compile(rf"signed\s+with\s+the\s+(?P<dst>{norm.TEAM_PATTERN})", re.IGNORECASE)
RESIGNED_RE = re.compile(rf"(?:The|the)\s+(?P<dst>{norm.TEAM_PATTERN})\s+re-signed\s+", re.IGNORECASE)
FROM_TEAM_RE = re.compile(rf"from\s+the\s+(?P<src>{norm.TEAM_PATTERN})", re.IGNORECASE)


def load_rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda e: (
        str(e.get("exact_date") or ""), str(e.get("player_id") or ""),
        str(e.get("event_type") or ""), str(e.get("source_reference") or ""),
    ))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def season_year(season: str) -> int:
    return int(str(season)[:4])


def historical_charlotte_id(season: str) -> int | None:
    """Disambiguate the two unrelated Charlotte NBA franchises by season."""
    year = season_year(season)
    if year <= 2001:
        return 1610612740  # original Hornets franchise, now New Orleans
    if year >= 2004:
        return 1610612766  # expansion Bobcats/current Hornets
    return None


def resolve_team_name(name: str | None, season: str, context: norm.CoreContext) -> int | None:
    if not name:
        return None
    if name.strip().casefold() == "charlotte hornets":
        fixed = historical_charlotte_id(season)
        if fixed is not None:
            return fixed
    team, _ = norm.resolve_team(name, season, context)
    return team


def maybe_repair_existing(event: dict[str, Any], context: norm.CoreContext) -> tuple[dict[str, Any], list[str]]:
    e = dict(event)
    changes: list[str] = []
    season = str(e.get("season") or "")
    raw = str(e.get("raw_text") or "")

    if e.get("source_team_id") is None and e.get("source_team_name"):
        team = resolve_team_name(str(e.get("source_team_name")), season, context)
        if team is not None:
            e["source_team_id"] = team
            changes.append("source_team_alias")
    if e.get("destination_team_id") is None and e.get("destination_team_name"):
        team = resolve_team_name(str(e.get("destination_team_name")), season, context)
        if team is not None:
            e["destination_team_id"] = team
            changes.append("destination_team_alias")

    # Claims identify both the destination and, when present in the prose, the
    # departing team.  The original parser retained only the destination.
    if e.get("event_type") == "claim" and e.get("source_team_id") is None:
        match = FROM_TEAM_RE.search(raw)
        if match:
            team = resolve_team_name(match.group("src"), season, context)
            if team is not None:
                e["source_team_id"] = team
                changes.append("claim_source_team")

    # Once team ambiguity is repaired, retry identity resolution for historical
    # events that previously could not be tied to a core player id.
    if not e.get("player_id") and e.get("player_name"):
        player_id, method = norm.resolve_player(
            str(e.get("player_name")), season, context,
            int(e["source_team_id"]) if e.get("source_team_id") else None,
            int(e["destination_team_id"]) if e.get("destination_team_id") else None,
        )
        if player_id:
            e["player_id"] = player_id
            e["identity_resolution"] = f"repair:{method}"
            changes.append("player_identity")

    unresolved_team = (
        bool(e.get("source_team_name")) and e.get("source_team_id") is None
    ) or (
        bool(e.get("destination_team_name")) and e.get("destination_team_id") is None
    )
    if e.get("player_id") and not unresolved_team:
        e["confidence"] = "high"
        if changes:
            e["team_resolution"] = str(e.get("team_resolution") or "") + ";repair_applied"
    return e, changes


def event_from_unparsed(row: dict[str, Any], context: norm.CoreContext) -> list[dict[str, Any]]:
    cells = row.get("cells") or []
    text = str(cells[1] if len(cells) > 1 else "")
    season = str(row.get("season") or "")
    if not text or not season:
        return []

    event_type: str | None = None
    src_name: str | None = None
    dst_name: str | None = None

    m = PASSIVE_CLAIM_RE.search(text)
    if m:
        event_type = "claim"
        dst_name = m.group("dst")
        src_name = m.groupdict().get("src")
    else:
        m = PASSIVE_WAIVE_RE.search(text)
        if m:
            event_type = "depart"
            src_name = m.group("src")
        else:
            m = PASSIVE_RELEASE_RE.search(text)
            if m:
                event_type = "depart"
                src_name = m.group("src")
            else:
                m = SIGNED_WITH_RE.search(text)
                if m:
                    event_type = "acquire"
                    dst_name = m.group("dst")
                else:
                    m = RESIGNED_RE.search(text)
                    if m:
                        event_type = "acquire"
                        dst_name = m.group("dst")

    if event_type is None:
        return []

    src = resolve_team_name(src_name, season, context)
    dst = resolve_team_name(dst_name, season, context)
    players = norm.players_in_text(text, row)
    output: list[dict[str, Any]] = []
    for player in players:
        pid, identity_method = norm.resolve_player(player["text"], season, context, src, dst)
        if not pid:
            continue
        output.append({
            "exact_date": str(row.get("exact_date") or row.get("subsection") or ""),
            "season": season,
            "event_type": event_type,
            "player_id": pid,
            "player_name": player["text"],
            "source_player_ref": player.get("href"),
            "source_team_id": src,
            "destination_team_id": dst,
            "source_team_name": src_name,
            "destination_team_name": dst_name,
            "source_system": "Basketball-Reference via Internet Archive",
            "source_reference": f"{row.get('source_url')}#row-{row.get('row_index')}:repair",
            "identity_resolution": f"repair:{identity_method}",
            "team_resolution": "repair:passive_or_alternate_transaction_wording",
            "raw_text": text,
            "confidence": "high" if pid and (src is not None or dst is not None) else "review",
        })
    return output


def dedupe_key(e: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(e.get("exact_date") or ""), str(e.get("season") or ""),
        str(e.get("event_type") or ""), str(e.get("player_id") or e.get("source_player_ref") or ""),
        int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
        str(e.get("raw_text") or ""),
    )


def main() -> int:
    if not NORMALIZED.exists():
        raise RuntimeError(f"Missing normalized transaction file: {NORMALIZED}")
    context = norm.load_core_context()
    original = load_rows(NORMALIZED)
    repaired: list[dict[str, Any]] = []
    change_counts: dict[str, int] = {}
    for event in original:
        fixed, changes = maybe_repair_existing(event, context)
        repaired.append(fixed)
        for change in changes:
            change_counts[change] = change_counts.get(change, 0) + 1

    added: list[dict[str, Any]] = []
    unresolved = json.loads(UNRESOLVED.read_text(encoding="utf-8")) if UNRESOLVED.exists() else {}
    for row in unresolved.get("unparsed_roster_transaction_rows") or []:
        added.extend(event_from_unparsed(row, context))

    seen = {dedupe_key(e) for e in repaired}
    added_unique = []
    for e in added:
        key = dedupe_key(e)
        if key in seen:
            continue
        seen.add(key)
        repaired.append(e)
        added_unique.append(e)

    write_rows(NORMALIZED, repaired)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "input_events": len(original),
        "output_events": len(repaired),
        "added_events_from_unparsed_rows": len(added_unique),
        "repair_counts": dict(sorted(change_counts.items())),
        "remaining_events_without_player_id": sum(not e.get("player_id") for e in repaired),
        "remaining_events_with_named_unresolved_team": sum(
            (bool(e.get("source_team_name")) and e.get("source_team_id") is None)
            or (bool(e.get("destination_team_name")) and e.get("destination_team_id") is None)
            for e in repaired
        ),
        "policy": "repairs deterministic historical franchise aliases and transaction wording only; no inferred transaction dates",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
