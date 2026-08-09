from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

import apply_verified_overlap_supplement_v13 as v13

SUMMARY = v13.ROOT / "verified_overlap_supplement_v14_summary.json"
SOURCE = "Verified historical transaction supplement v14"


def ev(*, date: str, season: str, pid: str, name: str, kind: str,
       source: int | None = None, dest: int | None = None,
       ref: str, raw: str, derived: bool = False) -> dict[str, Any]:
    row = {
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
        "verified_boundary_v14": True,
    }
    if derived:
        row["derived_boundary_type"] = "natural_10_day_contract_endpoint"
    return row


def supplement() -> list[dict[str, Any]]:
    rows = list(v13.supplement())
    rows.extend([
        # 2010-11: OKC -> Charlotte, transaction day belongs to OKC;
        # Charlotte tenure begins the following calendar day.
        ev(date="2011-02-24", season="2010-11", pid="201591", name="DJ White", kind="trade",
           source=1610612760, dest=1610612766,
           ref="https://www.basketball-reference.com/teams/OKC/2011_transactions.html#February-24-2011",
           raw="Oklahoma City traded Morris Peterson and D.J. White to Charlotte for Nazr Mohammed on February 24, 2011."),

        # 2011-12: San Antonio -> Golden State.
        ev(date="2012-03-15", season="2011-12", pid="2210", name="Richard Jefferson", kind="trade",
           source=1610612759, dest=1610612744,
           ref="https://www.basketball-reference.com/teams/SAS/2012_transactions.html#March-15-2012",
           raw="San Antonio traded T.J. Ford, Richard Jefferson and a first-round pick to Golden State for Stephen Jackson on March 15, 2012."),

        # 2012-13: Orlando -> Milwaukee for both target players.
        ev(date="2013-02-21", season="2012-13", pid="200755", name="JJ Redick", kind="trade",
           source=1610612753, dest=1610612749,
           ref="https://www.basketball-reference.com/teams/ORL/2013_transactions.html#February-21-2013",
           raw="Orlando traded Gustavo Ayon, J.J. Redick and Ish Smith to Milwaukee on February 21, 2013."),
        ev(date="2013-02-21", season="2012-13", pid="202397", name="Ish Smith", kind="trade",
           source=1610612753, dest=1610612749,
           ref="https://www.basketball-reference.com/teams/ORL/2013_transactions.html#February-21-2013",
           raw="Orlando traded Gustavo Ayon, J.J. Redick and Ish Smith to Milwaukee on February 21, 2013."),

        # 2013-14: Toronto waiver already supplied by v13; exact Chicago signing
        # closes the incoming boundary without using minutes.
        ev(date="2013-12-13", season="2013-14", pid="201571", name="D.J. Augustin", kind="acquire",
           dest=1610612741,
           ref="https://www.basketball-reference.com/teams/CHI/2014_transactions.html#December-13-2013",
           raw="Chicago signed D.J. Augustin as a free agent on December 13, 2013."),

        # 2014-15 A.J. Price: exact Cleveland/Indiana/Cleveland/Phoenix roster chain.
        ev(date="2014-09-26", season="2014-15", pid="201985", name="AJ Price", kind="acquire",
           dest=1610612739,
           ref="https://www.basketball-reference.com/teams/CLE/2015_transactions.html#September-26-2014",
           raw="Cleveland signed A.J. Price on September 26, 2014."),
        ev(date="2014-11-01", season="2014-15", pid="201985", name="AJ Price", kind="depart",
           source=1610612739,
           ref="https://www.basketball-reference.com/teams/CLE/2015_transactions.html#November-1-2014",
           raw="Cleveland waived A.J. Price on November 1, 2014."),
        ev(date="2014-11-06", season="2014-15", pid="201985", name="AJ Price", kind="acquire",
           dest=1610612754,
           ref="https://www.basketball-reference.com/teams/IND/2015_transactions.html#November-6-2014",
           raw="Indiana signed A.J. Price on November 6, 2014."),
        ev(date="2014-11-28", season="2014-15", pid="201985", name="AJ Price", kind="depart",
           source=1610612754,
           ref="https://www.basketball-reference.com/teams/IND/2015_transactions.html#November-28-2014",
           raw="Indiana waived A.J. Price on November 28, 2014."),
        ev(date="2014-11-30", season="2014-15", pid="201985", name="AJ Price", kind="claim",
           dest=1610612739,
           ref="https://www.basketball-reference.com/teams/CLE/2015_transactions.html#November-30-2014",
           raw="Cleveland claimed A.J. Price on waivers from Indiana on November 30, 2014."),
        ev(date="2015-01-07", season="2014-15", pid="201985", name="AJ Price", kind="depart",
           source=1610612739,
           ref="https://www.basketball-reference.com/teams/CLE/2015_transactions.html#January-7-2015",
           raw="Cleveland waived A.J. Price on January 7, 2015."),
        ev(date="2015-03-21", season="2014-15", pid="201985", name="AJ Price", kind="acquire",
           dest=1610612756,
           ref="https://www.basketball-reference.com/teams/PHO/2015_transactions.html#March-21-2015",
           raw="Phoenix signed A.J. Price to a 10-day contract on March 21, 2015."),
        ev(date="2015-03-30", season="2014-15", pid="201985", name="AJ Price", kind="depart",
           source=1610612756,
           ref="https://www.basketball-reference.com/teams/PHO/2015_transactions.html#March-21-2015",
           raw="Derived exact natural endpoint of A.J. Price's Phoenix 10-day contract signed March 21, 2015: March 30, 2015, tenth calendar day inclusive.",
           derived=True),

        # 2014-15 Philadelphia -> Houston.
        ev(date="2015-02-19", season="2014-15", pid="203909", name="KJ McDaniels", kind="trade",
           source=1610612755, dest=1610612745,
           ref="https://www.basketball-reference.com/teams/PHI/2015_transactions.html#February-19-2015",
           raw="Philadelphia traded K.J. McDaniels to Houston for Isaiah Canaan and a second-round pick on February 19, 2015."),
    ])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    rows = supplement()
    if args.self_test:
        assert len(rows) == 25, len(rows)
        assert len({v13.key(r) for r in rows}) == len(rows)
        assert all(r.get("confidence") == "high" for r in rows)
        print("VERIFIED OVERLAP SUPPLEMENT V14 SELF-TEST PASSED")
        return 0
    if not v13.EVENTS.exists():
        raise RuntimeError("normalized transaction stream missing")
    existing = v13.read_rows(); seen = {v13.key(r) for r in existing}
    added = [r for r in rows if v13.key(r) not in seen]
    existing.extend(added); v13.write_rows(existing)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "verified_events_defined": len(rows),
        "verified_events_added": len(added),
        "verified_events_already_present": len(rows) - len(added),
        "events": rows,
        "policy": "Exact source-documented roster transactions only, plus deterministic natural endpoints of explicitly documented 10-day contracts; no boundary is inferred from player minutes. Transaction date remains with the departing team and incoming tenure begins the following calendar day. Zero-overlap and zero-review gates are unchanged.",
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k != "events"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
