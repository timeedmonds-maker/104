from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
EVENTS = ROOT / "normalized_transactions.jsonl.gz"
SUMMARY = ROOT / "verified_overlap_supplement_v13_summary.json"
SOURCE = "Verified historical transaction supplement v13"


def read_rows() -> list[dict[str, Any]]:
    with gzip.open(EVENTS, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_rows(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (
        str(r.get("exact_date") or ""), str(r.get("player_id") or ""),
        str(r.get("event_type") or ""), int(r.get("source_team_id") or 0),
        int(r.get("destination_team_id") or 0), str(r.get("source_reference") or ""),
    ))
    with gzip.open(EVENTS, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def ev(*, date: str, season: str, pid: str, name: str, kind: str,
       source: int | None = None, dest: int | None = None,
       ref: str, raw: str) -> dict[str, Any]:
    return {
        "exact_date": date,
        "season": season,
        "event_type": kind,
        "player_id": pid,
        "player_name": name,
        "source_player_ref": None,
        "source_team_id": source,
        "destination_team_id": dest,
        "source_team_name": None,
        "destination_team_name": None,
        "source_system": SOURCE,
        "source_reference": ref,
        "identity_resolution": "verified_player_id+remaining_overlap_case",
        "team_resolution": "verified_historical_transaction",
        "raw_text": raw,
        "confidence": "high",
        "verified_boundary_v13": True,
    }


def supplement() -> list[dict[str, Any]]:
    # These are not inferred from minutes. They are exact roster transactions or
    # contract endpoints independently documented by the cited historical source.
    return [
        ev(date="2009-02-19", season="2008-09", pid="2226", name="Will Solomon", kind="trade",
           source=1610612761, dest=1610612758,
           ref="https://www.basketball-reference.com/teams/TOR/2009_transactions.html#February-19-2009",
           raw="Toronto sold Will Solomon's player rights to Sacramento on February 19, 2009."),

        ev(date="2013-12-09", season="2013-14", pid="201571", name="D.J. Augustin", kind="depart",
           source=1610612761,
           ref="https://basketball.realgm.com/nba/teams/Toronto-Raptors/28/Transaction-History/2014#Dec-9-2013",
           raw="Toronto placed D.J. Augustin on waivers on December 9, 2013."),

        ev(date="2013-11-20", season="2013-14", pid="202721", name="Darius Morris", kind="depart",
           source=1610612755,
           ref="https://www.espn.com/nba/story/_/id/10007910/kwame-brown-waived-philadelphia-76ers",
           raw="Philadelphia waived Darius Morris on November 20, 2013."),
        ev(date="2014-01-06", season="2013-14", pid="202721", name="Darius Morris", kind="acquire",
           dest=1610612746,
           ref="https://www.latimes.com/sports/sportsnow/la-sp-sn-morris-clippers-20140106-story.html",
           raw="The Clippers signed Darius Morris to a 10-day contract on January 6, 2014."),
        ev(date="2014-01-16", season="2013-14", pid="202721", name="Darius Morris", kind="acquire",
           dest=1610612746,
           ref="https://www.hoopsrumors.com/2014/01/clippers-sign-darius-morris.html",
           raw="The Clippers signed Darius Morris to a second 10-day contract on January 16, 2014."),
        ev(date="2014-01-25", season="2013-14", pid="202721", name="Darius Morris", kind="depart",
           source=1610612746,
           ref="https://www.latimes.com/sports/sportsnow/la-sp-sn-clippers-morris-20140126-story.html",
           raw="Darius Morris' second Clippers 10-day contract expired following the January 25, 2014 game."),

        ev(date="2014-01-23", season="2013-14", pid="202952", name="Malcolm Thomas", kind="depart",
           source=1610612759,
           ref="https://www.cbssports.com/nba/news/jazz-claimed-recently-waived-malcolm-thomas/",
           raw="San Antonio waived Malcolm Thomas on January 23, 2014."),
        ev(date="2014-01-25", season="2013-14", pid="202952", name="Malcolm Thomas", kind="claim",
           dest=1610612762,
           ref="https://www.ksl.com/article/28492616/jazz-claim-malcolm-thomas-off-waivers",
           raw="Utah claimed Malcolm Thomas off waivers on January 25, 2014."),

        ev(date="2013-11-12", season="2013-14", pid="202620", name="Arinze Onuaku", kind="depart",
           source=1610612740,
           ref="https://www.nba.com/pelicans/blog/pelicans-add-lou-amundson-josh-childress-111213",
           raw="New Orleans waived Arinze Onuaku in the November 12, 2013 roster moves."),

        ev(date="2013-12-05", season="2013-14", pid="203473", name="Dewayne Dedmon", kind="depart",
           source=1610612744,
           ref="https://basketball.realgm.com/wiretap/230969/Dewayne-Dedmon-Waived-By-Warriors",
           raw="Golden State waived Dewayne Dedmon on December 5, 2013."),

        # Sacramento's own release describes the Jan. 30 agreement as the second
        # 10-day deal. Jan. 30 through Feb. 8 is ten calendar days inclusive.
        ev(date="2015-02-08", season="2014-15", pid="203113", name="Quincy Miller", kind="depart",
           source=1610612758,
           ref="https://www.nba.com/kings/news/kings-sign-miller-second-contract",
           raw="Derived exact natural endpoint of Quincy Miller's second Sacramento 10-day contract signed January 30, 2015: February 8, 2015, tenth calendar day inclusive."),
    ]


def key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("season") or ""), str(row.get("player_id") or ""),
        str(row.get("event_type") or ""), str(row.get("exact_date") or ""),
        int(row.get("source_team_id") or 0), int(row.get("destination_team_id") or 0),
    )


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    rows = supplement()
    if args.self_test:
        assert len(rows) == 11
        assert len({key(r) for r in rows}) == len(rows)
        assert all(r["confidence"] == "high" and r["verified_boundary_v13"] for r in rows)
        print("VERIFIED OVERLAP SUPPLEMENT V13 SELF-TEST PASSED")
        return 0
    if not EVENTS.exists():
        raise RuntimeError("normalized transaction stream missing")
    existing = read_rows(); seen = {key(r) for r in existing}
    added = [r for r in rows if key(r) not in seen]
    existing.extend(added); write_rows(existing)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "verified_events_defined": len(rows),
        "verified_events_added": len(added),
        "verified_events_already_present": len(rows) - len(added),
        "events": rows,
        "policy": "Exact source-documented roster transactions only; no boundary is inferred from player minutes. Existing zero-overlap and zero-review QA gates remain unchanged.",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k != "events"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
