# TREB Actions takeover runbook

Prepared 2026-08-11 while Codex production is still running 2001-02.

## Migration boundary

Do not interrupt the current Codex run until 2001-02 is locked. The takeover boundary is:

- 2000-01 locked
- 2001-02 locked
- stop before materially processing 2002-03

## Required handoff from the Codex sandbox

Before the sandbox is discarded, preserve the latest versions/deltas for:

- `team_trb_all_players/local_treb_rebuild.py`
- `team_trb_all_players/run_cross_era_regression.py`
- `team_trb_all_players/run_local_treb_production.py`
- `team_trb_all_players/impact_database/cross_era_regression.json`
- compact locked output for 2000-01
- compact locked output for 2001-02
- latest production repair tables/audit data
- latest master manifest or per-season manifests

The latest Codex-local commits seen before preparation were:

- cross-era PASS: `9951b792f6fccbafcde95a9091a7a76a10254a5c`
- checkpointed production runner: `2a5fcce5d9366295d91184285281d52503371939`
- locked 2000-01: `2bcb93d4e2464e513cae5caeb98cbcdf43739b12`

These hashes were local-only when last checked and must not be assumed to exist remotely.

## Locked gates that must survive takeover

- V2 universe: `14524 / 9647 / 4877 / 5199`
- zero overlap / impossible-minute / empty-tenure cases
- seven-era cross-era gate passes using the agreed rebound-count tolerance
- dedicated 2016 Steven Adams gate remains exact:
  - OKC OREB universe 1277
  - seconds 143368
  - OREB 816
  - DREB 1846
  - team rebounds 2662
  - opponent rebounds 2275
  - 8672/8672 matched
  - 0 unmatched

## Actions architecture

The dormant workflow is `.github/workflows/treb-production-matrix.yml` on branch `treb-actions-prep`.

It is deliberately configured to run only on pushes to `treb-validated-2016-recovery` that modify:

`team_trb_all_players/impact_database/ACTIONS_CONTROL.json`

The control file is currently disabled.

When activated, the workflow will:

1. rebuild and verify the V2 target universe;
2. rerun the locked exact 2016 gate once as transition preflight;
3. package the canonical V2 targets for all matrix jobs;
4. run seasons independently on `ubuntu-latest`;
5. use `max-parallel: 4` initially;
6. download only the raw archive pair required by that season;
7. run the production engine for one year only;
8. always upload a durable artifact containing logs/checkpoints/audit state;
9. publish each successful season to its own `treb-prod-YEAR` branch;
10. fail visibly on repair queues rather than silently accepting structural errors.

This avoids shared-manifest write races between parallel jobs.

## First Actions wave

After takeover code is persisted, do not launch all remaining seasons immediately. First run a four-season pilot:

- 2002-03
- 2003-04
- 2004-05
- 2005-06

If branch publication, artifacts, checkpoints and repair handling all behave correctly, launch 2006-07 through 2024-25 at max parallel 4.

## Repair handling

For a failed season job:

1. fetch its Actions job logs;
2. identify exact game/period/event failures;
3. apply the narrowest deterministic repair to the production engine;
4. rerun locked 2016 and any directly affected regression gate;
5. update `ACTIONS_CONTROL.json` to contain only the failed year(s);
6. rerun those years;
7. once passing, continue the remaining wave.

Do not loosen structural requirements for missing games, wrong seconds, unresolved lineup states, or unmatched rebound-bearing rows.

## 2025-26

2025-26 is intentionally excluded from the initial control file. It requires the dedicated `nbastatsv3_2025` + `cdnnba_2025` adapter and source-equivalence validation before production.

## Final assembly

After 2002-03 through 2024-25 are locked and 2025-26 passes its adapter gate:

- collect the locked outputs from the per-season production branches/artifacts;
- retain 2000-01 and 2001-02 locked outputs from Codex;
- combine with 9,647 full-core reuse rows;
- resolve all 4,877 partial player-team-season rows / 5,199 game-bearing segments exactly once;
- produce 14,524 final player-team-season rows;
- aggregate career ON/OFF/swing from underlying counts, never averaged percentages;
- generate configurable minutes-threshold ranks, including >=10,000 minutes;
- report exact Steven Adams career values/ranks;
- run final 26-season QA and provenance checks.

## Takeover rule

The Actions preparation branch is dormant and must not be merged/activated until the current Codex 2001-02 season is locked and its latest production-engine changes are externally persisted.
