# `treb-final-database-complete-20260812` output inventory — 2026-08-13

Status: DURABLE READ-ONLY INVENTORY

Source branch: `treb-final-database-complete-20260812`  
Source commit: `6c5e7b042ee6e7564b310c22bee478645fef37da`  
Source commit message: `Final TREB database 2000-01 through 2025-26`

No reconstruction, normalization, or new TREB computation was performed for this inventory.

## What the source commit actually persisted

All persisted export files are under `team_trb_all_players/final_database_export/`.

| File | Source-branch role | Recovery classification | Use in current exact assembly |
|---|---|---|---|
| `career_treb_detail.csv` | Player/team/tenure-segment on/off rebound counts and rates | **Reusable locked/assembled output** | **YES, as an assembly source after mapping to the current canonical tenure universe. Not a drop-in complete current database.** |
| `career_treb_summary.csv` | Career aggregation by player | Derived aggregation | QA/comparison only; regenerate from the final canonical detail table rather than treating it as independent source truth. |
| `treb_2000_01_to_2025_26.sqlite` | SQLite packaging of the same export data | Reusable packaging, not independent evidence | Useful for extraction/cross-checking; do not count it as additional source coverage beyond the CSV export. |
| `accepted_exclusions.csv` | Exact documented source/lineup exception ledger | Supplemental provenance / historical exception ledger | Reuse for provenance and targeted repair mapping. **Do not treat the 40 historical exclusions as current production waivers.** |
| `season_lock_manifest.csv` | Provenance for all 26 durable season locks | **Reusable provenance locator** | **YES.** Use it to map each season to its durable lock/engine/commit and to recover the locked segment source material. |
| `qa_report.json` | Integrity report for this historical export | Historical QA, not current final QA | Reuse as comparison evidence only. Its PASS applies to this export's then-current policy, which allowed 40 exclusions and a bounded 2025 bridge approximation. |
| `build.log` | Build result/metadata | Historical diagnostic | Comparison/provenance only. |
| `SHA256SUMS.txt` | Checksums of persisted export files | Integrity metadata | **YES.** Use to verify recovered files have not changed. |
| `README.txt` | Export description | Metadata | Documentation only. |

## Integrity facts recorded by this branch

The branch's own `qa_report.json` records:

- `status`: `PASS`
- seasons expected: **26**
- seasons complete: **26**
- first season: `2000-01`
- last season: `2025-26`
- tenure-detail rows: **5,199**
- career players: **1,824**
- duplicate tenure keys: **0**
- unexpected exceptions: **0**
- unmatched rebound rows: **0**
- accepted excluded games: **40**
- accepted exclusion ledger exact match: `true`
- 2025 engine: `modern_cdn_explicit_substitutions`
- 2025 policy: `accepted_bounded_modern_bridge_approximation`

The `season_lock_manifest.csv` marks every season from 2000-01 through 2025-26 as `COMPLETE`. The historical seasons use recovered-completion engines/commits; 2025-26 is recorded with the modern explicit-substitution engine.

## Why this branch is valuable but is not the current final answer

This branch proves that a complete 26-season **locked segment export** was already assembled and persisted. It therefore closes a major part of the supposed provenance gap and must be used in recovery.

However, the current canonical project universe is larger/different than this old export's 5,199 tenure-detail rows. The later exact-assembly work separately identified the full-core population and the current canonical tenure universe. Therefore these 5,199 rows must be treated as **reusable locked segment material to map/merge**, not as a replacement for the current canonical target table.

Also, this historical PASS was obtained under policies that documented **40 accepted game exclusions** and an `accepted_bounded_modern_bridge_approximation` for 2025-26. The current recovery must preserve those as provenance, but should overlay later exact repairs/caches wherever available rather than silently inheriting them as final waivers.

## Cryptographic identities from `SHA256SUMS.txt`

- `README.txt`: `9e3d51de397ca085a09bb2daa161043d8b4617408f702a0ac002429ea0cfac0e`
- `accepted_exclusions.csv`: `6efc460f7e39a0c65ff56eb1e34d8d04f412fd964167ceb29b0a36fdb8131e52`
- `build.log`: `c2d6424207e190327fc40eaf34d080af454ca6a6aca190a47c13916e0e41ff69`
- `career_treb_detail.csv`: `637168e08baf9650618930e958456784e85347a8e7d41917b68e7960d831cb46`
- `career_treb_summary.csv`: `75d3de4d113604f881e1f811a43711fc7d1fd4a6eabceb2f1617d81316862c76`
- `qa_report.json`: `c2d6424207e190327fc40eaf34d080af454ca6a6aca190a47c13916e0e41ff69`
- `season_lock_manifest.csv`: `6ae22a03f0fa1dcba4f4adcad75e0afc0a1079f95e5781ed3283a88c6681d103`
- `treb_2000_01_to_2025_26.sqlite`: `cdac9ec8054aa24ff408e122cff7d3fab5c160524878c75e68896f047304d838`

## Current assembly rule established by this inventory

1. Preserve `career_treb_detail.csv`/SQLite as a **locked segment source**.
2. Preserve `season_lock_manifest.csv` as the locator/provenance map for all 26 season locks.
3. Preserve `accepted_exclusions.csv` as an exception-repair map, not a waiver list.
4. Overlay the already-identified later exact batch/repair/cached facts before calculating any residual gap.
5. Recompute career summaries and final QA only after the current canonical detail table has been assembled.
6. Do **not** launch broad reconstruction from this branch inventory.

## Next finite action

Map the 5,199 persisted locked-segment rows from this branch against the current canonical tenure universe and persist an **overlap / unmatched / replacement-source manifest**. This is an assembly/provenance action only; it should not reconstruct games.
