#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    qas = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.rglob("qa.json"))]
    residual = []
    for p in sorted(root.rglob("residual_failures.json")):
        residual.extend(json.loads(p.read_text(encoding="utf-8")))

    out = {
        "seasons_found": len(qas),
        "target_games": sum(int(q.get("target_games", 0)) for q in qas),
        "recovered_games": sum(int(q.get("recovered_games", 0)) for q in qas),
        "residual_failed_games": sum(int(q.get("residual_failed_games", 0)) for q in qas),
        "exact_identity_join_repairs": sum(int(q.get("exact_identity_join_repairs", 0)) for q in qas),
        "recovered_team_rows": sum(int(q.get("recovered_team_rows", 0)) for q in qas),
        "recovered_player_rows": sum(int(q.get("recovered_player_rows", 0)) for q in qas),
        "residual_failures": residual,
        "season_qa": qas,
    }
    out["status"] = "COMPLETE" if len(qas) == 12 and out["target_games"] == 146 else "INCOMPLETE_MATRIX"
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in {"residual_failures", "season_qa"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
