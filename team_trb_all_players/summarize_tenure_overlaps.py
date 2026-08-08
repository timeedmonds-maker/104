from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE / "impact_database" / "roster_tenure"
AUDIT = ROOT / "tenure_consistency_audit.json"
OUT = ROOT / "tenure_overlap_summary.json"


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    overlaps = list(audit.get("strict_cross_team_overlaps") or [])
    by_season = Counter(str(x.get("season") or "unknown") for x in overlaps)
    by_player = Counter(
        (str(x.get("season") or ""), str(x.get("player_id") or ""), str(x.get("player_name") or ""))
        for x in overlaps
    )
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "strict_cross_team_overlap_count": len(overlaps),
        "by_season": dict(sorted(by_season.items())),
        "top_player_seasons": [
            {"season": key[0], "player_id": key[1], "player_name": key[2], "overlaps": count}
            for key, count in by_player.most_common(100)
        ],
        "first_100_overlaps": overlaps[:100],
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "strict_cross_team_overlap_count": len(overlaps),
        "by_season": dict(sorted(by_season.items())),
        "top_20_player_seasons": summary["top_player_seasons"][:20],
        "output": str(OUT),
    }, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
