#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

import finalize_treb_full_89_database as base

CANONICAL = {
    "TotalReboundPct": ("treb_on", "treb_off"),
    "OffReboundPct": ("oreb_pct_on", "oreb_pct_off"),
    "DefReboundPct": ("dreb_pct_on", "dreb_pct_off"),
}

EXACT_ALIASES = {
    "TotalReboundPct": {
        "totalreboundpct", "totalreboundpercentage", "reboundpct", "reboundpercentage",
        "rebpct", "trebpct", "teamreboundpct", "teamreboundpercentage",
    },
    "OffReboundPct": {
        "offreboundpct", "offreboundpercentage", "offensivereboundpct",
        "offensivereboundpercentage", "orebpct", "orebpercentage",
        "teamoffreboundpct", "teamoffensivereboundpct",
    },
    "DefReboundPct": {
        "defreboundpct", "defreboundpercentage", "defensivereboundpct",
        "defensivereboundpercentage", "drebpct", "drebpercentage",
        "teamdefreboundpct", "teamdefensivereboundpct",
    },
}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def is_percentage(n: str) -> bool:
    return "pct" in n or "percent" in n or "percentage" in n


def is_rebound(n: str) -> bool:
    return "rebound" in n or "reb" in n or "oreb" in n or "dreb" in n


def is_opponent(n: str) -> bool:
    return "opponent" in n or n.startswith("opp") or "opprebound" in n


def semantic_candidates(canonical: str, names: list[str]) -> list[str]:
    out = []
    for name in names:
        n = norm(name)
        if is_opponent(n) or not is_percentage(n) or not is_rebound(n):
            continue
        if canonical == "TotalReboundPct":
            side = not any(x in n for x in ("offensive", "offrebound", "oreb", "defensive", "defrebound", "dreb"))
        elif canonical == "OffReboundPct":
            side = any(x in n for x in ("offensive", "offrebound", "oreb")) and not any(x in n for x in ("defensive", "defrebound", "dreb"))
        else:
            side = any(x in n for x in ("defensive", "defrebound", "dreb")) and not any(x in n for x in ("offensive", "offrebound", "oreb"))
        if side:
            out.append(name)
    return out


def resolve(names: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    diagnostics: dict[str, object] = {"metric_names": sorted(names), "resolution": {}}
    for canonical in CANONICAL:
        exact = [name for name in names if norm(name) in EXACT_ALIASES[canonical]]
        semantic = semantic_candidates(canonical, names)
        candidates = exact if len(exact) == 1 else semantic
        diagnostics["resolution"][canonical] = {
            "exact_alias_matches": sorted(exact),
            "semantic_matches": sorted(semantic),
        }
        if len(candidates) != 1:
            raise RuntimeError(
                "unable to resolve rebound metric unambiguously for "
                f"{canonical}: exact={sorted(exact)} semantic={sorted(semantic)}; "
                f"all_metrics={sorted(names)}"
            )
        resolved[canonical] = candidates[0]
    if len(set(resolved.values())) != 3:
        raise RuntimeError(f"rebound metric resolver produced non-unique mapping: {resolved}")
    diagnostics["resolved"] = resolved
    return resolved


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--stage2-export-dir", type=Path, required=True)
    ap.add_argument("--exact-dir", type=Path, required=True)
    args, _ = ap.parse_known_args()

    src = pd.read_parquet(args.stage2_export_dir / "player_team_season_corrected_on_off.parquet", columns=["metric"])
    names = sorted(src["metric"].astype(str).dropna().unique().tolist())
    if len(names) != base.EXPECTED_METRICS:
        raise RuntimeError(f"expected {base.EXPECTED_METRICS} Stage2 metrics before alias resolution; got {len(names)}")

    resolved = resolve(names)
    base.REB_OVERLAY = {resolved[canonical]: fields for canonical, fields in CANONICAL.items()}
    print(json.dumps({"canonical_to_stage2_rebound_metric": resolved}, indent=2))

    rc = base.main()

    out = args.exact_dir
    alias_report = {
        "status": "PASS",
        "canonical_to_stage2": resolved,
        "stage2_to_canonical": {v: k for k, v in resolved.items()},
        "native_stage2_metric_names_preserved": True,
        "rounded_percentage_backsolve_used": False,
    }
    (out / "TREB_REBOUND_METRIC_ALIASES.json").write_text(json.dumps(alias_report, indent=2) + "\n")

    qa_path = out / "ALL_89_METRICS_QA.json"
    qa = json.loads(qa_path.read_text())
    qa["canonical_rebound_metric_aliases"] = resolved
    qa["native_stage2_metric_names_preserved"] = True
    qa_path.write_text(json.dumps(qa, indent=2) + "\n")
    print(json.dumps(alias_report, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
