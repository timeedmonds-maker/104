from __future__ import annotations

import argparse
import gzip
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
DB = BASE / "impact_database"
ROSTER = DB / "roster_tenure"
BREF = DB / "historical_transactions" / "basketball_reference_uniform" / "season_rows"
EVENTS = ROSTER / "normalized_transactions.jsonl.gz"
TARGETS = ROSTER / "remaining_overlap_event_chains_v11.json"
SUMMARY = ROSTER / "bref_targeted_overlap_recovery_v12_summary.json"

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
FIRST_ALIASES = {
    "ronald": "flip", "flip": "ronald",
    "norm": "norman", "norman": "norm",
    "steve": "steven", "steven": "steve",
}


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (
        str(r.get("exact_date") or ""), str(r.get("player_id") or ""),
        str(r.get("event_type") or ""), str(r.get("source_reference") or ""),
    ))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    out = re.findall(r"[a-z0-9]+", text)
    while out and out[-1] in SUFFIXES:
        out.pop()
    return out


def compact(value: str) -> str:
    return "".join(tokens(value))


def compatible_name(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    ca, cb = "".join(ta), "".join(tb)
    if ca == cb:
        return True
    if ta[-1] != tb[-1]:
        return False
    # Punctuation-only initial variants: D.J. White == DJ White, J.J. Redick == JJ Redick.
    pa, pb = "".join(ta[:-1]), "".join(tb[:-1])
    if pa == pb and pa:
        return True
    fa, fb = ta[0], tb[0]
    if fa == fb:
        return True
    if len(fa) >= 3 and len(fb) >= 3 and (fa.startswith(fb) or fb.startswith(fa)):
        return True
    return FIRST_ALIASES.get(fa) == fb or FIRST_ALIASES.get(fb) == fa


def is_future_pick_mention(player_name: str, raw_text: str) -> bool:
    """Reject player links that only identify the eventual owner of a traded draft pick."""
    name = re.escape(player_name.strip())
    return bool(re.search(rf"\(\s*{name}\s+was\s+later\s+selected\s*\)", raw_text, re.IGNORECASE))


def charlotte_fix(team_id: int | None, team_name: str | None, season: str) -> int | None:
    if season in {"2000-01", "2001-02"} and str(team_name or "").strip().casefold() == "charlotte hornets":
        return 1610612766
    return team_id


def target_index() -> tuple[dict[str, list[dict[str, Any]]], int]:
    data = json.loads(TARGETS.read_text(encoding="utf-8"))
    by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in data.get("cases") or []:
        teams = {int(w["team_id"]) for w in case.get("windows") or [] if w.get("team_id")}
        by_season[str(case["season"])].append({
            "season": str(case["season"]),
            "player_id": str(case["player_id"]),
            "player_name": str(case.get("player_name") or case["player_id"]),
            "teams": teams,
        })
    return dict(by_season), sum(len(v) for v in by_season.values())


def choose_target(
    season: str,
    player_name: str,
    source_team: int | None,
    destination_team: int | None,
    targets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    candidates = [t for t in targets.get(season, []) if compatible_name(player_name, t["player_name"])]
    if len(candidates) == 1:
        return candidates[0]
    relevant = {x for x in (source_team, destination_team) if x}
    contextual = [t for t in candidates if not relevant or t["teams"] & relevant]
    return contextual[0] if len(contextual) == 1 else None


def dedupe_key(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(event.get("season") or ""), str(event.get("player_id") or ""),
        str(event.get("event_type") or ""), str(event.get("exact_date") or ""),
        int(event.get("source_team_id") or 0), int(event.get("destination_team_id") or 0),
    )


def event_dict(parsed: Any) -> dict[str, Any]:
    if hasattr(parsed, "__dict__"):
        return dict(parsed.__dict__)
    return dict(parsed)


def cleanup_false_future_pick_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for event in events:
        if (
            event.get("source_system") == "Basketball-Reference via Internet Archive"
            and event.get("player_name")
            and is_future_pick_mention(str(event["player_name"]), str(event.get("raw_text") or ""))
        ):
            removed.append({
                "season": event.get("season"), "player_id": event.get("player_id"),
                "player_name": event.get("player_name"), "source_reference": event.get("source_reference"),
            })
            continue
        kept.append(event)
    return kept, removed


def recover() -> dict[str, Any]:
    if not EVENTS.exists() or not TARGETS.exists():
        raise RuntimeError("v11 normalized events and overlap diagnostic are required")
    context = norm.load_core_context()
    targets, target_count = target_index()
    events = read_jsonl_gz(EVENTS)
    events, false_pick_removed = cleanup_false_future_pick_events(events)
    seen = {dedupe_key(e) for e in events if e.get("player_id")}

    recovered: list[dict[str, Any]] = []
    rows_scanned = 0
    target_rows_seen = 0
    unresolved_target_rows: list[dict[str, Any]] = []

    for season, season_targets in sorted(targets.items()):
        path = BREF / f"{season}.jsonl.gz"
        if not path.exists():
            continue
        for row in read_jsonl_gz(path):
            rows_scanned += 1
            cells = row.get("cells") or []
            raw_text = " ".join(str(x) for x in cells[1:]).strip()
            if not raw_text:
                continue
            linked_targets: list[dict[str, Any]] = []
            for link in norm.bref_player_links(row):
                name = str(link.get("text") or "")
                if not name or is_future_pick_mention(name, raw_text):
                    continue
                for target in season_targets:
                    if compatible_name(name, target["player_name"]):
                        linked_targets.append(target)
                        break
            if not linked_targets:
                continue
            target_rows_seen += 1

            parsed_events = [event_dict(e) for e in norm.parse_bref_row(row, context)]
            matched_any = False
            for parsed in parsed_events:
                pname = str(parsed.get("player_name") or "")
                if not pname or is_future_pick_mention(pname, raw_text):
                    continue
                parsed["source_team_id"] = charlotte_fix(
                    int(parsed.get("source_team_id")) if parsed.get("source_team_id") else None,
                    parsed.get("source_team_name"), season,
                )
                parsed["destination_team_id"] = charlotte_fix(
                    int(parsed.get("destination_team_id")) if parsed.get("destination_team_id") else None,
                    parsed.get("destination_team_name"), season,
                )
                target = choose_target(
                    season, pname,
                    int(parsed.get("source_team_id")) if parsed.get("source_team_id") else None,
                    int(parsed.get("destination_team_id")) if parsed.get("destination_team_id") else None,
                    targets,
                )
                if target is None:
                    continue
                parsed["player_id"] = target["player_id"]
                parsed["identity_resolution"] = "v12_targeted_bref_player_link+overlap_case"
                parsed["confidence"] = "high" if (parsed.get("source_team_id") or parsed.get("destination_team_id")) else "review"
                key = dedupe_key(parsed)
                if key in seen:
                    matched_any = True
                    continue
                seen.add(key)
                recovered.append(parsed)
                matched_any = True

            if not matched_any:
                unresolved_target_rows.append({
                    "season": season,
                    "row_index": row.get("row_index"),
                    "source_url": row.get("source_url"),
                    "raw_text": raw_text,
                    "target_players": sorted({t["player_name"] for t in linked_targets}),
                })

    events.extend(recovered)
    write_jsonl_gz(EVENTS, events)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "target_player_seasons": target_count,
        "cached_bref_rows_scanned": rows_scanned,
        "rows_containing_target_player_links": target_rows_seen,
        "recovered_target_events_added": len(recovered),
        "false_future_draft_pick_player_events_removed": len(false_pick_removed),
        "target_rows_still_unparsed": len(unresolved_target_rows),
        "recovered_event_sample": recovered[:100],
        "false_future_pick_event_sample": false_pick_removed[:100],
        "unparsed_target_row_sample": unresolved_target_rows[:100],
        "policy": (
            "Recovery is limited to player-season cases present in the durable v11 overlap diagnostic. "
            "Player identity is bound through the Basketball-Reference player link plus a unique overlap-case name/team match. "
            "Parenthetical future draft-pick selections are explicitly excluded and no dates are invented."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_sample")}, indent=2), flush=True)
    return summary


def self_test() -> None:
    assert compact("D.J. White") == compact("DJ White")
    assert compact("J.J. Redick") == compact("JJ Redick")
    assert compact("K.J. McDaniels") == compact("KJ McDaniels")
    assert compatible_name("Roger Mason", "Roger Mason Jr.")
    assert compatible_name("Ronald Murray", "Flip Murray")
    assert is_future_pick_mention("Frank Mason III", "a 2017 pick ( Frank Mason III was later selected) to Team")
    assert not is_future_pick_mention("Frank Mason III", "Team signed Frank Mason III to a contract")
    print("BREF TARGETED OVERLAP RECOVERY V12 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    recover()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
