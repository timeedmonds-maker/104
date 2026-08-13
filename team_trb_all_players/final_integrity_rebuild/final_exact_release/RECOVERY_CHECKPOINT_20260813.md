# TREB corrected recovery checkpoint — 2026-08-13

Status: DURABLE RECOVERY CHECKPOINT

This checkpoint supersedes the recovery interpretation implied by `TARGETED_MISSING_EXACT_GAMES.json`. That V3 manifest remains useful as a record of the failed strict assembly, but its `3,949` strict-missing-game count MUST NOT be treated as the amount of historical reconstruction still required.

## Confirmed retained production material

The data gap has already been materially closed by retained exact outputs and repair products. The recovery/assembly must reuse these sources rather than launch a broad historical reconstruction:

1. Historical exact-game fanout
   - Original run: `31569006637`
   - Produced: `240/240` historical batch artifacts
   - Corrected consolidation scope: `22,786` games checked
   - Batch artifacts contain exact production files including `team_game_treb.csv.gz`, `player_game_treb_on.csv.gz`, `game_audit.json`, `failures.json`, checksums, and build logs.

2. Exact game-fact branch union and repair overlays
   - Workflow: `TREB exact game-fact union coverage`
   - Verified run: `31666990243`
   - Artifact: `9168287624` (`exact-game-union-coverage.zip`)
   - Middle-23-season exact union coverage: `27,296 / 27,646` regular-season games (`98.73%`).
   - The union explicitly includes `treb-game-facts-YYYY-batch-NNN` branches plus repair overlays.

3. Forensic exact-count cache recovery
   - Aggregate-only recovery run: `31656553237`
   - Artifact: `9164591246` (`treb-rebound-forensic-cache-v2.zip`)
   - This is retained intermediate evidence and must be reused where applicable; it is not a reason to rerun historical reconstruction.

4. V13 aggregate recovery
   - Run: `31657564349`
   - Artifact: `9164943603` (`treb-v13-aggregate-recovery.zip`)

5. Safe-lock material
   - All `26` season safe-lock branches are retained (`treb-final-safe-lock-YYYY`).
   - The failed canonical assembler had already recovered `26 COMPLETE locks / 5,199 partial segments` before failing later in full-core opponent-component assembly.
   - Edge-season examples already verified include `treb-final-safe-lock-2001` and `treb-final-safe-lock-2025`.

6. Existing completion branch
   - Branch: `treb-final-database-complete-20260812`
   - Verified head at recovery inspection: `ffc9fb71c6731ae757ca905cc585c599967fb2dd`
   - This branch must be inspected/reused before any new reconstruction is contemplated.

## Correct interpretation of the residual gap

- The old V3 strict manifest reported `4,412` affected full-core tenure rows and `3,949` strict-missing full-core games.
- Those figures are NOT independent reconstruction requirements. A small set of missing/unaccepted games propagates across many full-season player rows.
- The later recovered working residual is `341 unique unaccepted missing games`, while the broader exact-game union already contains almost all historical games.
- Do not conflate the `4,412` affected tenure rows with `4,412` games needing reconstruction.

## Assembly rule from this checkpoint

Recover and combine, in provenance order:

`safe locks + exact batch branches + repair overlays + retained fanout artifacts + exact forensic/V13 recovered caches`

Only after that union is materialized and audited should any genuinely unresolved residual be considered for targeted repair. No broad reconstruction is authorized by this checkpoint.

## Integrity constraints

- Preserve exact integer rebound components wherever exact data exists.
- Do not weaken integrity gates to manufacture completion.
- Keep forensic/approximation evidence distinguishable from exact production data unless separately validated for promotion.
- Persist each next recovery/assembly step to GitHub before proceeding to the following step.

Next finite action after this checkpoint: inspect/materialize the retained completion sources into a single provenance inventory; do not start historical reconstruction.