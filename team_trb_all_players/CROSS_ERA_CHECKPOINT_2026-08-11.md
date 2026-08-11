# TREB cross-era checkpoint — 2026-08-11

This checkpoint was reconstructed from the user's uploaded Codex transcript and is stored outside the Codex sandbox for durability.

## Authoritative target universe

- validated player-team-season rows: 14,524
- full-core reuse rows: 9,647
- partial player-team-season rows: 4,877
- game-bearing partial tenure segments: 5,199
- cross-team overlap pairs: 0
- impossible-minute rows: 0
- empty played tenures: 0

## Locked 2016 regression

The exact Steven Adams 2016-17 regression remains authoritative and must not be loosened:

- OKC OREB universe: 1,277
- Adams seconds ON: 143,368
- Adams team OREB ON: 816
- Adams team DREB ON: 1,846
- Adams team rebounds ON: 2,662
- opponent rebounds ON: 2,275
- rebound-bearing rows matched: 8,672 / 8,672
- unmatched rebound-bearing rows: 0

## Cross-era runner state

Codex created `team_trb_all_players/run_cross_era_regression.py` locally and reported local commit:

`2e40059dce567621b6b5838e2e0b3933fcf036db`

That local commit was not present on GitHub at the time this checkpoint was written.

The runner samples:

- 2000-01 ATL
- 2004-05 BOS
- 2008-09 CLE
- 2012-13 SAS
- 2016-17 OKC
- 2020-21 PHX
- 2024-25 DEN

It reconstructs each sampled game once and applies the reconstructed timeline to three retained-core players.

## 2000-01 Atlanta diagnostic

All 82 / 82 games reconstructed.

All 8,370 / 8,370 rebound-bearing rows matched.

Unmatched rebound-bearing rows: 0.

Lineup/game exceptions: 0.

Player comparisons:

- Jason Terry: seconds 185,438 exact; expected OREB 935 / DREB 2,140; reconstructed OREB 933 / DREB 2,144.
- Lorenzen Wright: seconds 119,224 exact; expected OREB 577 / DREB 1,365; reconstructed OREB 577 / DREB 1,365 exact.
- Alan Henderson: seconds 108,480 exact; expected OREB 574 / DREB 1,195; reconstructed OREB 572 / DREB 1,194.

The discrepancy is therefore a small historical event-attribution difference, not a missing-game, unmatched-event, or minutes/lineup failure.

## Next validation policy

Cross-era validation should move to materiality-based TREB% tolerance rather than exact OREB/DREB count equality, while retaining strict structural checks.

Proposed policy:

- seconds/minutes exact
- complete expected games
- unmatched rebound-bearing rows = 0
- unresolved lineup/game exceptions = 0
- TREB% difference <= 0.0025: PASS
- TREB% difference > 0.0025 and <= 0.005: PASS WITH QA FLAG
- TREB% difference > 0.005: FAIL / investigate
- exact OREB/DREB count differences remain recorded for audit but do not independently fail the cross-era gate
- the locked 2016 Adams regression remains exact with no tolerance

Production must not begin until the seven-season cross-era gate passes under this materiality policy.
