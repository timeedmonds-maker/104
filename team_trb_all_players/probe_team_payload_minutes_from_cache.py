from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
CACHE = BASE / "impact_database" / "corrected_off" / "cache"


def finite(v):
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def main() -> int:
    complete_files = 0
    files_with_metric_minutes = 0
    exact_matches = 0
    mismatches = 0
    ambiguous = 0
    examples = []

    for path in sorted(CACHE.glob("*.json.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            continue
        if data.get("complete") is not True:
            continue
        metrics = data.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            continue
        complete_files += 1
        pairs = set()
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            extra = metric.get("source_extra")
            if not isinstance(extra, dict):
                continue
            on = finite(extra.get("MinutesOn"))
            off = finite(extra.get("MinutesOff"))
            if on is not None and off is not None:
                pairs.add((on, off))
        if not pairs:
            continue
        files_with_metric_minutes += 1
        if len(pairs) != 1:
            ambiguous += 1
            if len(examples) < 10:
                examples.append({"file": path.name, "kind": "ambiguous", "pairs": sorted(pairs)[:5]})
            continue
        pair = next(iter(pairs))
        top = (finite(data.get("minutes_on")), finite(data.get("minutes_off")))
        if top[0] is not None and top[1] is not None and abs(pair[0] - top[0]) <= 1e-9 and abs(pair[1] - top[1]) <= 1e-9:
            exact_matches += 1
        else:
            mismatches += 1
            if len(examples) < 10:
                examples.append({"file": path.name, "kind": "mismatch", "metric_pair": pair, "top_pair": top})

    summary = {
        "probe": "player-scoped TEAM response metric rows contain MinutesOn/MinutesOff",
        "network_calls": 0,
        "complete_cache_files_checked": complete_files,
        "files_with_metric_minutes": files_with_metric_minutes,
        "exact_matches_to_existing_stat_minutes": exact_matches,
        "mismatches": mismatches,
        "ambiguous_metric_minute_pairs": ambiguous,
        "coverage_pct": round(100.0 * files_with_metric_minutes / complete_files, 3) if complete_files else 0.0,
        "examples": examples,
    }
    print(json.dumps(summary, indent=2))

    # This is a validation probe, not a production fallback. Passing requires
    # at least one observed payload-minute pair and zero contradictions.
    return 0 if files_with_metric_minutes > 0 and mismatches == 0 and ambiguous == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
