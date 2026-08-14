# Classification of 11 unresolved targeted Stage2 keys

Source: persisted `RECOVERY_QA.json` from workflow run `31767722056`.

## Summary

- Unresolved keys: **11**
- Pure team-profile endpoint failures after 4 retries: **8**
  - All returned HTTP 503 from the PBP Stats tenure-scoped team endpoint.
  - Combined authoritative ON minutes: **209.2667**
  - Combined tenure games: **8** (all are one-game windows)
  - No metric payload was returned for these 8 in this attempt.
- Exact 89-metric tenure payload already recovered, but minute fields incomplete: **3**
  - Combined authoritative ON minutes: **104.1967**
  - Combined tenure games: **43**
  - These are not metric-recovery failures; the exact ON/OFF/SWING metric payload already exists.

## Group A — team endpoint unavailable (8)

| Season | Team ID | Player ID | Player | Games | Target ON min | Failure |
|---|---:|---:|---|---:|---:|---|
| 2016-17 | 1610612739 | 204002 | Edy Tavares | 1 | 24.0000 | team endpoint HTTP 503 on all 4 attempts |
| 2016-17 | 1610612739 | 2563 | Dahntay Jones | 1 | 12.0000 | team endpoint HTTP 503 on all 4 attempts |
| 2017-18 | 1610612745 | 1628935 | Aaron Jackson | 1 | 34.5000 | team endpoint HTTP 503 on all 4 attempts |
| 2022-23 | 1610612751 | 1630564 | RaiQuan Gray | 1 | 35.0833 | team endpoint HTTP 503 on all 4 attempts |
| 2022-23 | 1610612757 | 1628435 | Chance Comanche | 1 | 20.7833 | team endpoint HTTP 503 on all 4 attempts |
| 2022-23 | 1610612763 | 1631367 | Jacob Gilyard | 1 | 40.7833 | team endpoint HTTP 503 on all 4 attempts |
| 2024-25 | 1610612755 | 1630600 | Isaiah Mobley | 1 | 17.3667 | team endpoint HTTP 503 on all 4 attempts |
| 2025-26 | 1610612762 | 1643060 | Hayden Gray | 1 | 24.7500 | team endpoint HTTP 503 on all 4 attempts |

Classification: **PURE TECHNICAL / SOURCE AVAILABILITY FAILURE**, not evidence of a data-integrity or methodology defect. Any retry should target only these eight keys.

## Group B — exact 89 metrics recovered; only minutes incomplete (3)

| Season | Team ID | Player ID | Player | Games | Target ON min | Metric state | Minute issue |
|---|---:|---:|---|---:|---:|---|---|
| 2013-14 | 1610612745 | 2694 | Josh Powell | 1 | 19.3667 | 89/89 exact tenure-scoped metrics present | stat/minute endpoint HTTP 503 on all 4 attempts |
| 2025-26 | 1610612741 | 1631338 | Mouhamadou Gueye | 3 | 45.4417 | 89/89 exact tenure-scoped metrics present | stat/minute endpoint HTTP 503 on all 4 attempts |
| 2025-26 | 1610612752 | 1641794 | Dillon Jones | 39 | 39.3883 | 89/89 exact tenure-scoped metrics present | stat endpoint responded, but no unique matching player minute row was resolved |

Classification: **METRICS RECOVERED; MINUTE-COMPLETION ISSUE ONLY**. Do not re-query their 89 team metrics. Recover or deterministically source only the missing minute fields.

## Next finite action

1. Preserve the three 89-metric payloads exactly as recovered.
2. Resolve their missing minute fields only.
3. Retry only the eight one-game Group A team profiles, preferably with retained/cached source evidence before making any broader network request.
4. Do not rerun the five already complete keys and do not rebuild historical Stage2.
