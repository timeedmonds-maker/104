# V13 residual materiality recovery

- Audit status: **MATERIALITY_INPUT_REQUIRED**
- Current V13 residuals: **13**
- Reused cache chunks: **24/24** across **58 games**
- Deprecated `not matched` count: **101** (not used as residual truth)
- Deterministic repair required: **0**
- Accepted immaterial: **0**
- Remaining for materiality/repair: **13**
- Policy threshold: **0.01 pp**
- Materiality input: **missing_authoritative_published_denominators**

The V13 durable report is authoritative for the 13 current residual keys. The reused forensic cache is a V9 feature cache and is used only for diagnostics; its residual flag is not substituted for V13 truth.

| game_id | pbp_index | classification | max effect pp | evidence |
|---:|---:|---|---:|---|
| 21700032 | 4988 | evidence_recheck_required |  | expected durable invariance node was not recovered; cache description: Mavericks Rebound |
| 21700042 | 7322 | materiality_pending |  | V13 rejected apparent NBA event 180 because it is already assigned to PBP row 7324; event reuse forbidden; cache description: Johnson REBOUND |
| 21700494 | 7412 | materiality_pending |  | cache description: Cavaliers Rebound |
| 21800689 | 1396 | materiality_pending |  | cache description: TIMBERWOLVES Rebound |
| 21800689 | 1398 | materiality_pending |  | cache description: Suns Rebound |
| 22101023 | 256 | materiality_pending |  | cache description: THUNDER Rebound |
| 22200322 | 3388 | materiality_pending |  | cache description: Clippers Rebound |
| 22200331 | 3717 | materiality_pending |  | cache description: BUCKS Rebound |
| 22200331 | 3912 | materiality_pending |  | cache description: Lakers Rebound |
| 22200733 | 5955 | materiality_pending |  | cache description: Cavaliers Rebound |
| 22201187 | 1569 | materiality_pending |  | cache description: [GSW] Team Rebound |
| 22201187 | 1573 | materiality_pending |  | cache description: [GSW] Team Rebound |
| 22400072 | 288 | materiality_pending |  | cache description: Warriors Rebound |

No generic event-ordering rule was introduced. No integrity gate or 0.01 pp tolerance was weakened.
