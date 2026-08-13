# Grouped exact-game recovery plan

Status: **PLAN PINNED — NO RECONSTRUCTION LAUNCHED**

Authoritative branch at plan creation: `agent/treb-final-exact-assembly` @ `b6fe557a7bfea22342171e68b47f23ede00af002`.

## 1. Objective and immutable scope

Complete the current exact TREB recovery only for the deduplicated games already proven necessary by the V2 residual mapper. The authoritative input scope is:

- `../UNIQUE_MISSING_GAMES.csv`
- `../historical_gap_partition/HISTORICAL_MISSING_GAME_PARTITION.csv`
- `../historical_gap_partition/LEGACY_ACCEPTED_OVERLAP_RECOVERY_GAMES.csv`
- `../historical_gap_partition/EDGE_SEASON_RECOVERY_GAMES.csv`
- `../historical_gap_partition/HISTORICAL_MISSING_GAME_PARTITION_SUMMARY.json`

Hard scope totals:

- **3,958 unique missing games total**
- **350 historical games (2002-03 through 2024-25)**
  - **341 strict historical recovery games**
  - **9 legacy-accepted-exclusion overlaps; all 9 remain mandatory production recovery targets**
- **3,608 edge-season games**
  - 2000-01: **1,189**
  - 2001-02: **1,189**
  - 2025-26: **1,230**

The historical legacy list contains 40 game IDs, but 31 are not currently missing. Legacy acceptance is provenance only and is not a production waiver.

No job may expand this recovery universe beyond these 3,958 canonical integer-equivalent game IDs without a separately persisted diagnostic proving why.

## 2. Game-once recovery rule

Recovery is **game-centric, never residual-key-centric**.

Each of the 3,958 games is reconstructed at most once. A successful game reconstruction produces a reusable exact game-fact bundle that is subsequently joined to every affected `season|player_id|team_id` residual key. No game may be independently reconstructed once per affected player or tenure.

The 27,296 historical exact games already present in the validated exact-game union are authoritative reusable facts and must not be recomputed by this recovery wave.

## 3. Required exact outputs per recovered game

Each successful recovered game must produce the same logical fact products used by the retained exact-game union:

1. **`team_game_treb`**
   - exactly two distinct team rows per game;
   - canonical `game_id` and `team_id`;
   - exact raw integer rebound components required to calculate team TREB% without percentage backsolve;
   - opponent/team relationships internally consistent for the two game sides.

2. **`player_game_treb_on`**
   - exact player-game/team rows needed to assemble the current residual player-team-season keys;
   - canonical `game_id`, `team_id`, and `player_id`;
   - exact on-court raw components required by the established TREB assembly schema;
   - player-team assignment must agree with the reconstructed game facts and canonical roster/tenure scope.

Files must retain the existing exact-game fact schema and gzip/CSV conventions used by the validated historical sources (`team_game_treb.csv.gz` and `player_game_treb_on.csv.gz`).

## 4. Existing-fact precedence and conflict policy

Source precedence is conservative:

1. Existing validated exact-union facts are retained unchanged.
2. New recovery facts may fill only game/team/player keys absent from the validated exact union or absent from the current residual assembly requirement.
3. If a newly recovered key duplicates an existing exact key with identical raw values, it is a harmless duplicate and the existing validated fact remains authoritative.
4. If a newly recovered key disagrees with an existing exact key on any material raw component, **hard fail and persist the conflict**. Never silently choose a source, average values, or overwrite an existing exact fact.

The existing union demonstrates the durable source convention: retained `treb-game-facts-YYYY-batch-NNN` branches plus repair-wave exact files are read as `team_game_treb.csv.gz` and `player_game_treb_on.csv.gz`, with coverage established from positive exact rows. Recovery must produce facts compatible with that convention.

## 5. Hard validation gates before a game is accepted

A recovered game is PASS only when all applicable gates pass:

- game ID normalizes to the canonical integer-equivalent representation used by the corrected V2 mapper;
- game belongs to the pinned 3,958-game recovery manifest;
- schedule season/date and home/away team IDs match the authoritative V3 schedule;
- exactly **2 distinct team rows** exist for the game;
- both scheduled teams are represented and no third team is present;
- all TREB source components are exact raw counts, not values inferred from a rounded percentage;
- team-side raw components reconcile internally and across opponents according to the established exact-game schema;
- player-game rows reference the same canonical game/team IDs;
- player rows used for residual assembly are compatible with the canonical roster/tenure intervals;
- duplicate exact keys are either byte/value-equivalent or raised as conflicts;
- no previously validated exact game is replaced;
- no scope expansion beyond the pinned manifest occurs.

Global recovery acceptance additionally requires:

- unique requested game count = **3,958**;
- historical requested game count = **350**;
- historical strict count = **341**;
- historical legacy-overlap count = **9**;
- edge-season count = **3,608**;
- every requested game ends in exactly one durable terminal state: PASS or explicitly diagnosed FAIL;
- no game appears in more than one successful recovery bundle.

## 6. Batch execution design

Batch by season first, then deterministic ascending canonical game ID. Use a **new operational cap of at most 100 unique games per runner batch**. This cap is an execution choice for durability and retry isolation; it is not a data-integrity tolerance.

Expected maximum batch counts at the 100-game cap:

- 350 historical games: at most 23 season-scoped batches because seasons are never mixed solely to fill a batch;
- 2000-01: 12 batches;
- 2001-02: 12 batches;
- 2025-26: 13 batches.

Historical and edge-season recovery may use the same exact reconstruction engine where source availability permits, but their manifests remain separately identifiable. Do not bundle a failed game's retry with a broad rerun of already successful games.

A future workflow may use a matrix across bounded batches, subject to repository runner limits. Parallelism must not change source precedence, game assignment, or output determinism.

## 7. Durability and idempotency

Every batch must persist, even on partial failure:

- requested-game manifest;
- successful-game manifest;
- failed-game manifest with explicit reason codes;
- `team_game_treb.csv.gz` for successful recoveries;
- `player_game_treb_on.csv.gz` for successful recoveries;
- validation summary JSON;
- conflict diagnostic, if any;
- source/provenance metadata sufficient to reproduce the batch;
- PASS/FAIL status.

Artifacts must be uploaded with `if: always()` or equivalent so failure diagnostics survive.

A global checkpoint must union successful batches by canonical key and record which of the 3,958 requested games remain unresolved. Reruns must consult this checkpoint and skip already validated PASS games. Failed jobs receive targeted reruns only for their unresolved games/batches.

## 8. Recovery-to-residual assembly

After game recovery is complete, do not rebuild the full database from scratch.

1. Union the recovered exact facts with the retained validated exact-game union under the precedence/conflict rules above.
2. Remap only the current **4,602 residual canonical keys** against the expanded exact fact union.
3. Reassemble exact player-team-season TREB rows from raw components.
4. Resolve the **93 currently team-game-covered residual rows** through player-fact/assembly diagnostics rather than reconstructing unrelated games; the 32 known player-fact-deficit rows remain explicitly identifiable.
5. Preserve the **7,241 safe-lock keys unchanged**.
6. Merge recovered exact residual rows back with the safe lock and other already promoted exact sources under the established source-overlay precedence.
7. Run final canonical-count, duplicate, raw-component, TREB recomputation, source-conflict, season coverage, and materiality QA before release.

## 9. Prohibited shortcuts

- No rounded TREB percentage backsolve.
- No opponent inference from rounded percentages.
- No blanket acceptance of the 40 legacy exclusion IDs.
- No broad rerun of the 27,296 validated historical exact games.
- No per-player duplicate reconstruction of the same game.
- No silent conflict resolution.
- No weakening the 0.01 percentage-point materiality/integrity standard.
- Do not reopen the separately accepted 13 V13 residual rebound-attribution cases.

## 10. Next finite action

Materialize a targeted GitHub Actions recovery workflow from this plan. Its first execution should be **one bounded batch only**, with durable outputs and all gates above enabled. Inspect that completed batch before scaling out further recovery batches.
