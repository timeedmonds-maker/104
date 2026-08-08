from __future__ import annotations

import argparse
import gzip
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import normalize_roster_transactions as norm

BASE = Path(__file__).resolve().parent
DB = BASE / "impact_database"
ROSTER = DB / "roster_tenure"
BREF = DB / "historical_transactions" / "basketball_reference_uniform" / "season_rows"
EVENTS = ROSTER / "normalized_transactions.jsonl.gz"
SUMMARY = ROSTER / "historical_transaction_repair_v4_summary.json"

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
FIRST_ALIASES = {
    "norm": "norman",
    "steve": "steven",
    "steven": "steve",
    "ronald": "flip",
    "flip": "ronald",
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


def norm_tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    tokens = re.findall(r"[a-z0-9]+", text)
    while tokens and tokens[-1] in SUFFIXES:
        tokens.pop()
    return tokens


def compact(value: str) -> str:
    return "".join(norm_tokens(value))


def names_compatible(a: str, b: str) -> bool:
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return False
    if "".join(ta) == "".join(tb):
        return True
    if ta[-1] != tb[-1]:
        return False
    fa, fb = ta[0], tb[0]
    if fa == fb:
        return True
    if len(fa) >= 3 and len(fb) >= 3 and (fa.startswith(fb) or fb.startswith(fa)):
        return True
    if FIRST_ALIASES.get(fa) == fb or FIRST_ALIASES.get(fb) == fa:
        return True
    return False


def fix_charlotte_team_ids(events: list[dict[str, Any]]) -> int:
    """Align 1988-2002 Charlotte history to the NBA/core Hornets team id.

    The core/schedule layer uses 1610612766 for pre-relocation Charlotte history.
    Earlier repair code forced those seasons to 1610612740, which left exact
    Charlotte transactions unable to bound the corresponding core affiliation.
    """
    changed = 0
    for e in events:
        season = str(e.get("season") or "")
        if season not in {"2000-01", "2001-02"}:
            continue
        for id_field, name_field in (
            ("source_team_id", "source_team_name"),
            ("destination_team_id", "destination_team_name"),
        ):
            name = str(e.get(name_field) or "").strip().casefold()
            team = int(e.get(id_field) or 0)
            if name == "charlotte hornets" or (team == 1610612740 and "charlotte" in str(e.get("raw_text") or "").casefold()):
                if team != 1610612766:
                    e[id_field] = 1610612766
                    changed += 1
                    e["team_resolution"] = str(e.get("team_resolution") or "") + ";historical_charlotte_core_id_v4"
    return changed


def candidate_ids(player_name: str, season: str, source: int | None, dest: int | None, context: norm.CoreContext) -> list[str]:
    relevant = {x for x in (source, dest) if x}
    candidates: list[str] = []
    for pid, names in context.names_by_id.items():
        if not any(names_compatible(player_name, n) for n in names):
            continue
        affiliations = context.affiliations.get((season, pid), set())
        if relevant and affiliations & relevant:
            candidates.append(pid)
    if candidates:
        return sorted(set(candidates))

    # Strong compact-name match can resolve a suffix/punctuation variant even
    # where the transaction team was a zero-minute stint absent from core.
    strong = []
    target = compact(player_name)
    for pid, names in context.names_by_id.items():
        if any(compact(n) == target for n in names):
            strong.append(pid)
    return sorted(set(strong))


def repair_unresolved_identities(events: list[dict[str, Any]], context: norm.CoreContext) -> tuple[int, list[dict[str, Any]]]:
    repaired = 0
    audit = []
    for e in events:
        if e.get("player_id") or not e.get("player_name"):
            continue
        season = str(e.get("season") or "")
        if not re.match(r"^20\d{2}-\d{2}$", season):
            continue
        source = int(e.get("source_team_id") or 0) or None
        dest = int(e.get("destination_team_id") or 0) or None
        ids = candidate_ids(str(e["player_name"]), season, source, dest, context)
        if len(ids) != 1:
            continue
        pid = ids[0]
        canonical = sorted(context.names_by_id.get(pid, set()))
        e["player_id"] = pid
        e["identity_resolution"] = "historical_v4_unique_fuzzy_name+season_team_context"
        e["confidence"] = "high" if source or dest else e.get("confidence", "review")
        repaired += 1
        audit.append({
            "season": season,
            "source_name": e.get("player_name"),
            "resolved_player_id": pid,
            "canonical_names": canonical,
            "source_team_id": source,
            "destination_team_id": dest,
            "source_reference": e.get("source_reference"),
        })
    return repaired, audit


def resolve_team_from_text(text: str, season: str, context: norm.CoreContext) -> tuple[int | None, str | None]:
    found: list[tuple[int, str]] = []
    folded = text.casefold()
    for name in norm.TEAM_ALIASES:
        if name.casefold() not in folded:
            continue
        if name == "Charlotte Hornets" and season in {"2000-01", "2001-02"}:
            found.append((1610612766, name))
            continue
        team, _ = norm.resolve_team(name, season, context)
        if team is not None:
            found.append((int(team), name))
    unique: dict[int, str] = {}
    for team, name in found:
        unique[team] = name
    if len(unique) == 1:
        team = next(iter(unique))
        return team, unique[team]
    return None, None


def exact_expiry_events(context: norm.CoreContext) -> list[dict[str, Any]]:
    """Parse exact BRef rows that explicitly state a 10-day contract expired."""
    output: list[dict[str, Any]] = []
    for path in sorted(BREF.glob("*.jsonl.gz")):
        for row in read_jsonl_gz(path):
            cells = row.get("cells") or []
            text = " ".join(str(x) for x in cells[1:]).strip()
            folded = text.casefold()
            if "10-day contract" not in folded or "expire" not in folded:
                continue
            if "not re-signed" not in folded and "did not re-sign" not in folded and "expired" not in folded and "expires" not in folded:
                continue
            season = str(row.get("season") or "")
            team, team_name = resolve_team_from_text(text, season, context)
            if team is None:
                continue
            players = norm.players_in_text(text, row)
            for player in players:
                ids = candidate_ids(player["text"], season, team, None, context)
                if len(ids) != 1:
                    continue
                output.append({
                    "exact_date": str(row.get("exact_date") or row.get("subsection") or ""),
                    "season": season,
                    "event_type": "depart",
                    "player_id": ids[0],
                    "player_name": player["text"],
                    "source_player_ref": player.get("href"),
                    "source_team_id": team,
                    "destination_team_id": None,
                    "source_team_name": team_name,
                    "destination_team_name": None,
                    "source_system": "Basketball-Reference via Internet Archive",
                    "source_reference": f"{row.get('source_url')}#row-{row.get('row_index')}:exact-10day-expiry-v4",
                    "identity_resolution": "historical_v4_unique_fuzzy_name+season_team_context",
                    "team_resolution": "exact_team_named_in_10day_expiry_row",
                    "raw_text": text,
                    "confidence": "high",
                    "derived_boundary_type": "source_stated_10_day_contract_expiry",
                })
    return output


def dedupe_key(e: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(e.get("season") or ""), str(e.get("player_id") or ""),
        str(e.get("event_type") or ""), str(e.get("exact_date") or ""),
        int(e.get("source_team_id") or 0), int(e.get("destination_team_id") or 0),
    )


def self_test() -> None:
    assert names_compatible("Norm Richardson", "Norman Richardson")
    assert names_compatible("Wang Zhizhi", "Wang Zhi-zhi")
    assert names_compatible("Roger Mason", "Roger Mason Jr.")
    assert names_compatible("Steve Smith", "Steven Smith")
    assert names_compatible("Ronald Murray", "Flip Murray")
    assert not names_compatible("Joe Smith", "Josh Smith")
    print("HISTORICAL TRANSACTION REPAIR V4 SELF-TEST PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    context = norm.load_core_context()
    events = read_jsonl_gz(EVENTS)
    before = len(events)
    charlotte = fix_charlotte_team_ids(events)
    identity_count, identity_audit = repair_unresolved_identities(events, context)

    expiries = exact_expiry_events(context)
    seen = {dedupe_key(e) for e in events}
    added_expiries = []
    for e in expiries:
        key = dedupe_key(e)
        if key in seen:
            continue
        seen.add(key)
        events.append(e)
        added_expiries.append(e)

    # Re-run identity repair because exact-expiry rows can provide additional
    # team-context anchors for names that were previously unresolved.
    identity_count2, identity_audit2 = repair_unresolved_identities(events, context)
    write_jsonl_gz(EVENTS, events)

    summary = {
        "input_events": before,
        "output_events": len(events),
        "historical_charlotte_team_id_fields_corrected": charlotte,
        "fuzzy_historical_player_identities_resolved": identity_count + identity_count2,
        "exact_source_stated_10_day_expiry_events_added": len(added_expiries),
        "remaining_events_without_player_id": sum(not e.get("player_id") for e in events),
        "identity_audit_sample": (identity_audit + identity_audit2)[:100],
        "expiry_audit_sample": added_expiries[:100],
        "policy": (
            "No transaction dates are guessed. Historical Charlotte uses the NBA/core team id 1610612766 for 2000-01 and 2001-02. "
            "Unresolved historical player names are linked only when fuzzy surname/name normalization plus same-season transaction-team context yields one unique core player. "
            "10-day departures are added only from Basketball-Reference rows that explicitly state the contract expired."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_sample")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
