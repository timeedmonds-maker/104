#!/usr/bin/env python3
"""Aggregate reusable rebound cache parts while preserving the exact production residual set.

The forensic cache deliberately contains every rebound-bearing PBP row in the 58 target
games.  Therefore generic source-only/unmatched rows are a superset of the production
residuals.  Production residual identity is taken only from the durable V9 regression
proof and asserted against the cache; no new repair decision is made here.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


def load_part(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--parts-root", type=Path, required=True)
    p.add_argument("--v9-proof", type=Path, required=True)
    p.add_argument("--workflow-run-id", type=int, required=True)
    p.add_argument("--source-run-id", type=int, required=True)
    p.add_argument("--output-cache", type=Path, required=True)
    p.add_argument("--output-summary", type=Path, required=True)
    z = p.parse_args()

    paths = sorted(z.parts_root.rglob("REBOUND_CACHE_*.json.gz"))
    assert len(paths) == 24, [str(x) for x in paths]
    parts = [load_part(x) for x in paths]
    assert all(x["engine"] == "production_rebound_v9" for x in parts)

    games = [g for x in parts for g in x["games"]]
    assert len(games) == 58 and len(set(games)) == 58, (len(games), len(set(games)))

    proof = json.loads(z.v9_proof.read_text())
    assert proof["status"] == "PASS"
    assert proof["engine"] == "production_rebound_v9"
    assert int(proof["v9_residual_rows"]) == 15
    target_keys = {(int(r["game_id"]), int(r["pbp_index"])) for r in proof["residual_rows"]}
    assert len(target_keys) == 15, len(target_keys)

    records = [r for x in parts for r in x["records"]]
    seen_target = set()
    generic_unmatched = 0
    for r in records:
        key = (int(r["game_id"]), int(r["pbp_index"]))
        is_target = key in target_keys
        r["production_residual"] = bool(is_target)
        if not r.get("matched"):
            generic_unmatched += 1
        if is_target:
            assert not r.get("matched"), ("production residual unexpectedly matched", key)
            assert key not in seen_target, ("duplicate production residual", key)
            seen_target.add(key)

    assert seen_target == target_keys, {
        "missing": sorted(target_keys - seen_target),
        "extra": sorted(seen_target - target_keys),
    }

    out = {
        "status": "FORENSIC_FEATURE_CACHE",
        "schema_version": 2,
        "workflow_run_id": int(z.workflow_run_id),
        "engine": "production_rebound_v9",
        "source_run_id": int(z.source_run_id),
        "chunks": 24,
        "target_games": 58,
        "record_count": len(records),
        "matched_control_rows": sum(1 for r in records if r.get("matched")),
        "generic_unmatched_rows": int(generic_unmatched),
        "current_residual_rows": 15,
        "production_residual_keys": [list(x) for x in sorted(target_keys)],
        "records": records,
    }
    z.output_cache.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(z.output_cache, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    summary = {k: v for k, v in out.items() if k != "records"}
    z.output_summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
