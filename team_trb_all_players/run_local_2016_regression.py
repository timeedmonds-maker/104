#!/usr/bin/env python3
"""Run and persist the locked 2016-17 local TREB regression gate."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd
from local_treb_rebuild import okc_2016_regression

EXPECTED = {
    "okc_oreb_universe": 1277,
    "adams_seconds_on": 143368,
    "team_oreb_on": 816,
    "team_dreb_on": 1846,
    "team_rebounds_on": 2662,
    "opponent_rebounds_on": 2275,
}

def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent
    default = root / "impact_database" / "local_raw" / "extracted"
    parser.add_argument("--nbastats", type=Path, default=default / "nbastats_2016.csv")
    parser.add_argument("--pbpstats", type=Path, default=default / "pbpstats_2016.csv")
    parser.add_argument("--output", type=Path, default=root / "impact_database" / "regression_2016_result.json")
    args = parser.parse_args()
    nba = pd.read_csv(args.nbastats)
    pbp = pd.read_csv(args.pbpstats)
    result = okc_2016_regression(nba, pbp)
    checks = {key: result[key] == value for key, value in EXPECTED.items()}
    checks["opponent_rebounds_retained_range"] = 2270 <= result["opponent_rebounds_on"] <= 2279
    audit = result["join_audit"]
    checks["rebound_rows_8672_of_8672"] = audit["rebound_bearing_rows"] == 8672 and audit["matched_rebound_bearing_rows"] == 8672
    checks["zero_unmatched_rebound_rows"] = audit["unmatched_rebound_bearing_rows"] == 0
    result["checks"] = checks
    result["status"] = "PASS" if all(checks.values()) else "FAIL"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())