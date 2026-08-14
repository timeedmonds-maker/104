#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import finalize_treb_release_contract as base


def main() -> int:
    # The complete career universe is defined by the already-authoritative
    # 14,524 canonical player-team-season target keys, not by an unrelated
    # hard-coded historical player count.
    targets = base.load_targets()
    canonical_players = int(targets["player_id"].nunique())
    if canonical_players <= 0:
        raise RuntimeError("canonical career player universe is empty")
    base.EXPECTED_CAREER_PLAYERS = canonical_players
    base.EXPECTED_CAREER_NATIVE_ROWS = canonical_players * base.EXPECTED_NATIVE_METRICS

    # The exact career summary intentionally stores percentage-point swing
    # columns (treb_swing_pp / oreb_swing_pp / dreb_swing_pp).  The release
    # wide schema expects raw proportion swing columns alongside raw on/off
    # values.  Supply those directly from on-off for the assembler, then
    # restore the original exact-support CSV so the source product is not
    # mutated in the final package.
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--exact-dir", type=Path, required=True)
    args, _ = ap.parse_known_args()
    summary_path = args.exact_dir / "career_treb_summary.csv"
    original = summary_path.read_bytes()
    try:
        career = pd.read_csv(summary_path, low_memory=False)
        required = [
            "treb_on", "treb_off",
            "oreb_pct_on", "oreb_pct_off",
            "dreb_pct_on", "dreb_pct_off",
        ]
        missing = [c for c in required if c not in career.columns]
        if missing:
            raise RuntimeError(f"exact career summary missing required columns: {missing}")
        career["treb_swing"] = pd.to_numeric(career["treb_on"], errors="coerce") - pd.to_numeric(career["treb_off"], errors="coerce")
        career["oreb_pct_swing"] = pd.to_numeric(career["oreb_pct_on"], errors="coerce") - pd.to_numeric(career["oreb_pct_off"], errors="coerce")
        career["dreb_pct_swing"] = pd.to_numeric(career["dreb_pct_on"], errors="coerce") - pd.to_numeric(career["dreb_pct_off"], errors="coerce")
        career.to_csv(summary_path, index=False)
        return base.main()
    finally:
        summary_path.write_bytes(original)


if __name__ == "__main__":
    raise SystemExit(main())
