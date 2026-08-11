#!/usr/bin/env python3
"""Aggregate TREB safe-sweep artifacts into one concise repair index."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for artifact in sorted(args.root.iterdir()):
        if not artifact.is_dir():
            continue
        m = re.match(r"treb-safe-season-(\d{4})-", artifact.name)
        if not m:
            continue
        year = int(m.group(1))
        manifests = list(artifact.rglob("master_manifest.json"))
        if not manifests:
            rows.append({"year": year, "artifact": artifact.name, "status": "NO_MANIFEST"})
            continue
        manifest = load(manifests[0])
        expected_season = f"{year}-{(year + 1) % 100:02d}"
        season_data = manifest.get("seasons", {}).get(expected_season, {})

        exceptions = []
        repairs = []
        unmatched = []
        for batch_path in sorted(artifact.rglob("batch_*.json")):
            batch = load(batch_path)
            for item in batch.get("exceptions", []) or []:
                exceptions.append({"batch": batch.get("batch_index"), **item})
            for item in batch.get("repairs", []) or []:
                repairs.append({"batch": batch.get("batch_index"), **item})
            for item in batch.get("unmatched_rebound_rows", []) or []:
                if isinstance(item, dict):
                    unmatched.append({"batch": batch.get("batch_index"), **item})
                else:
                    unmatched.append({"batch": batch.get("batch_index"), "value": item})

        log_lines = []
        for log_path in artifact.glob("treb-safe-repair-*.log"):
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                low = line.lower()
                if any(token in low for token in ("exception game=", "repair_required", "unmatched rebound", "substitution outgoing", "starter inference")):
                    log_lines.append(line[-2500:])

        source_audits = []
        for audit_path in artifact.rglob("*source_gap_audit.json"):
            source_audits.append(load(audit_path))

        rows.append({
            "year": year,
            "season": expected_season,
            "artifact": artifact.name,
            "status": season_data.get("status"),
            "games_required": season_data.get("games_required"),
            "targets": season_data.get("targets"),
            "manifest_exception_count": season_data.get("exceptions"),
            "manifest_repair_count": season_data.get("repairs"),
            "manifest_unmatched_rebound_rows": season_data.get("unmatched_rebound_rows"),
            "exceptions": exceptions,
            "repairs": repairs,
            "unmatched_rebound_rows": unmatched,
            "source_gap_audits": source_audits,
            "diagnostic_log_lines": log_lines[-100:],
        })

    rows.sort(key=lambda r: r["year"])
    complete = [r["year"] for r in rows if r.get("status") == "COMPLETE"]
    repair = [r["year"] for r in rows if r.get("status") == "REPAIR_REQUIRED"]
    other = [r["year"] for r in rows if r.get("status") not in {"COMPLETE", "REPAIR_REQUIRED"}]
    payload = {
        "artifact_years_found": len(rows),
        "complete_years": complete,
        "repair_required_years": repair,
        "other_years": other,
        "total_exceptions": sum(len(r.get("exceptions", [])) for r in rows),
        "total_repairs_logged": sum(len(r.get("repairs", [])) for r in rows),
        "total_unmatched_rows_logged": sum(len(r.get("unmatched_rebound_rows", [])) for r in rows),
        "seasons": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "seasons"}, indent=2))
    for row in rows:
        print(
            row["year"], row.get("status"),
            "exceptions", len(row.get("exceptions", [])),
            "repairs", len(row.get("repairs", [])),
            "unmatched", len(row.get("unmatched_rebound_rows", [])),
        )
        for exc in row.get("exceptions", []):
            print("  EXCEPTION", json.dumps(exc, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
