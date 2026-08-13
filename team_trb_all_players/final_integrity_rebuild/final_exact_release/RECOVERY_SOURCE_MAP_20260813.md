# TREB recovery source map — 2026-08-13

Status: DURABLE RECOVERY CHECKPOINT

This file supersedes the interpretation in `TARGETED_MISSING_EXACT_GAMES.json`. That V3 manifest overstates the practical reconstruction gap and MUST NOT be used as the rebuild plan.

## Confirmed retained sources that close/reduce the apparent gap

1. **Original historical fanout artifacts**
   - Workflow/run: `TREB exact game facts – historical fanout`, run `31569006637`
   - Completion observed: **240/240 batch artifacts**
   - Each batch artifact preserves exact game-level outputs including `team_game_treb.csv.gz`, `player_game_treb_on.csv.gz`, `game_audit.json`, `failures.json`, checksums/build logs.
   - These are primary exact-count sources and must be ingested before any reconstruction is considered.

2. **Batch/repair branch union**
   - Workflow: `TREB exact game-fact union coverage`
   - Successful run: `31666990243`
   - Artifact: `exact-game-union-coverage.zip`
   - Commit used by that run: `85c95d47d25a30d725e82c91a21c7ac108139c66`
   - Verified middle-season coverage: **27,296 / 27,646 regular-season games (98.73%)** across the 23 historical middle seasons after unioning `treb-game-facts-YYYY-batch-NNN` branches plus repair overlays.
   - The previously reported thousands of affected tenure rows are therefore amplification from a much smaller game-level residue, not thousands of independent missing reconstructions.

3. **Safe-lock season stores**
   - All 26 `treb-final-safe-lock-YYYY` branches exist and are part of the completed historical recovery path.
   - Example verified lock: `treb-final-safe-lock-2001` at `e3299a96...` with `locked_seasons/2001-02.json` marked `COMPLETE` and integer rebound counts.
   - Example verified modern edge store: `treb-final-safe-lock-2025` at `0785018e...`, with the 2025-26 lock/checkpoint material preserved.
   - These stores must be combined with the full-core material; they are not evidence of missing data.

4. **Forensic cache aggregate**
   - Successful aggregate-only recovery run: `31656553237`
   - Artifact: `treb-rebound-forensic-cache-v2.zip`
   - Approx size observed: 1.42 MB
   - This is retained intermediate source material and must be checked before any replay.

5. **V13 aggregate recovery**
   - Successful run: `31657564349`
   - Artifact: `treb-v13-aggregate-recovery.zip`
   - This belongs to the forensic-cache → aggregate-recovery → V13 → residual-materiality chain and must be included in provenance recovery.

6. **Counter-order forensic chunks**
   - Run: `31637129009`
   - **24/24 forensic chunk artifacts** were still retained when inspected on 2026-08-13.
   - These are evidence/repair inputs, not a reason to rerun reconstruction.

7. **Previously completed final branch**
   - Branch: `treb-final-database-complete-20260812`
   - Commit: `6c5e7b042ee6e7564b310c22bee478645fef37da`
   - Commit message: `Final TREB database 2000-01 through 2025-26`
   - This branch must be inspected/used as an assembly source before computing anything new.

## Correct operating interpretation

- The data was not broadly lost.
- The earlier narrow provenance pass was wrong because it searched too few committed filenames/branches and ignored surviving Actions artifacts and intermediate caches.
- Existing exact game facts, repair overlays, safe-lock outputs and retained artifacts are the recovery path.
- **Do not launch broad reconstruction.**
- **Do not use `TARGETED_MISSING_EXACT_GAMES.json` as the replay list.**
- First assemble/ingest the retained sources above, then measure the true residual gap.
- Only an exact, post-ingestion residual may be replayed, and only if still necessary.

## Chunking rule for continuation

From this checkpoint onward: one finite action per ChatGPT turn; persist every meaningful result to GitHub before the next action; no long polling chains.
