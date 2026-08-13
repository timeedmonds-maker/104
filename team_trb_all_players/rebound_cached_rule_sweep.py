#!/usr/bin/env python3
"""Explore predeclared rebound-rule subclasses from the reusable forensic cache.

Diagnostic only.  This deliberately does not mutate production.  It searches a
fixed, interpretable grid of lineup-anchor x rebound-side x description-format x
identity classes, validates every class on matched historical controls, and
reports only zero-error classes with meaningful control support that cover a
current production residual.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

ANCHORS = [
    "prior_miss_exact",
    "endpoint_gap0",
    "endpoint_gap1",
    "endpoint_gap2",
    "endpoint_gap3",
    "endpoint_gap5",
    "clock_invariant",
    "interval_invariant",
    "dual_miss_endpoint",
]
SIDES = ["all", "oreb", "dreb"]
FORMATS = ["all", "bracket", "bracket_counter", "nonbracket", "nonbracket_counter"]
IDENTITIES = ["all", "resolved", "unresolved", "named_nonteam", "teamlike"]


def read_cache(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def side_ok(r, side):
    return side == "all" or bool(r["pbp_is_oreb"]) == (side == "oreb")


def format_ok(r, fmt):
    b = bool(r.get("bracket_format"))
    c = bool(r.get("counter_format"))
    return {
        "all": True,
        "bracket": b,
        "bracket_counter": b and c,
        "nonbracket": not b,
        "nonbracket_counter": (not b) and c,
    }[fmt]


def identity_ok(r, identity):
    resolved = r.get("resolved_player_id") is not None
    named = r.get("live_predictions", {}).get("non_team_named_rebound") is True
    return {
        "all": True,
        "resolved": resolved,
        "unresolved": not resolved,
        "named_nonteam": named,
        "teamlike": not named,
    }[identity]


def applies(r, side, fmt, identity, anchor):
    return (
        side_ok(r, side)
        and format_ok(r, fmt)
        and identity_ok(r, identity)
        and r.get("lineup_predictions", {}).get(anchor) is not None
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--min-controls", type=int, default=25)
    z = p.parse_args()
    payload = read_cache(z.cache)
    records = payload["records"]
    matched = [r for r in records if r.get("matched")]

    # Schema v2+ explicitly marks the exact durable production residual set.
    # Fallback is retained only for older diagnostic caches.
    if any("production_residual" in r for r in records):
        residual = [r for r in records if r.get("production_residual") is True]
    else:
        residual = [r for r in records if not r.get("matched")]

    expected = payload.get("current_residual_rows")
    if expected is not None:
        assert len(residual) == int(expected), (len(residual), expected)

    tested = 0
    safe = []
    for anchor in ANCHORS:
        for side in SIDES:
            for fmt in FORMATS:
                for identity in IDENTITIES:
                    controls = [r for r in matched if applies(r, side, fmt, identity, anchor)]
                    if not controls:
                        continue
                    tested += 1
                    wrong = []
                    for r in controls:
                        pred = r["lineup_predictions"][anchor]
                        good = bool(r.get("actual_real_rebound")) and pred == r.get("actual_lineup")
                        if not good:
                            wrong.append({
                                "game_id": r["game_id"],
                                "pbp_index": r["pbp_index"],
                                "description": r["description"],
                                "actual_real_rebound": r.get("actual_real_rebound"),
                            })
                    if wrong or len(controls) < z.min_controls:
                        continue
                    hits = [r for r in residual if applies(r, side, fmt, identity, anchor)]
                    if not hits:
                        continue
                    safe.append({
                        "anchor": anchor,
                        "side": side,
                        "format": fmt,
                        "identity": identity,
                        "applicable_controls": len(controls),
                        "correct_controls": len(controls),
                        "wrong_controls": 0,
                        "residual_candidates": [
                            {
                                "game_id": r["game_id"],
                                "pbp_index": r["pbp_index"],
                                "description": r["description"],
                                "predicted_lineup": r["lineup_predictions"][anchor],
                            }
                            for r in hits
                        ],
                    })

    safe.sort(key=lambda x: (-x["applicable_controls"], -len(x["residual_candidates"]), x["anchor"], x["side"], x["format"], x["identity"]))
    out = {
        "status": "DIAGNOSTIC_ONLY",
        "cache_schema_version": payload.get("schema_version"),
        "engine_under_test": payload.get("engine"),
        "matched_control_rows": len(matched),
        "generic_unmatched_rows": payload.get("generic_unmatched_rows"),
        "current_residual_rows": len(residual),
        "predeclared_rule_classes_tested": tested,
        "minimum_control_support": z.min_controls,
        "zero_error_rules_covering_residuals": safe,
    }
    z.output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({
        "matched_control_rows": len(matched),
        "generic_unmatched_rows": payload.get("generic_unmatched_rows"),
        "current_residual_rows": len(residual),
        "predeclared_rule_classes_tested": tested,
        "zero_error_rules_covering_residuals": len(safe),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
