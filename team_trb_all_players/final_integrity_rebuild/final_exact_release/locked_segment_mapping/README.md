# Locked-segment → canonical mapping

Status: **PASS**

This checkpoint performs mapping/provenance only. **No TREB reconstruction or replay occurred.**

- Canonical target rows: **14,524**
- Canonical unique player/team/season keys: **14,524**
- Locked source segments read: **5,199**
- Locked source unique player/team/season keys: **4,877**
- Canonical keys represented by locked source: **4,877**
- Locked keys requiring aggregation of >1 segment: **282**
- Extra segments collapsed by player/team/season grouping: **322**
- Unmatched locked source keys: **0**
- Canonical keys not supplied by this locked source: **9,647**

Classification observed: `{'full_core': 9535, 'partial': 4989}`.

Next step after this checkpoint: overlay/current-source precedence mapping against the already-retained exact full-core/game-fact sources.
