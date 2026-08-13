# TREB 20200077 adjudicated rebound repair canary

Status: READY TO IMPLEMENT — executable GitHub write blocked by current connector safety gate.

## Durable evidence

The exact evidence record is already committed at:

`team_trb_all_players/final_integrity_rebuild/rebound_forensics/ADJUDICATED_REBOUND_REPAIR_20200077.json`

Source adjudication:
- workflow run: `31726024619`
- artifact: `9191278776`
- source commit: `213b0ffc157cf830b22bcc925a825f1e1afb68da`

## Exact repair tuple

- game_id: `20200077`
- period: `2`
- PBP start_time: `11:42`
- PBP end_time: `11:26`
- PBP description: `Richardson REBOUND (Off:1 Def:1)`
- NBA eventnum: `110`
- NBA elapsed: `773`
- NBA source clock: `11:07`
- NBA description: `Richardson REBOUND (Off:1 Def:1)`
- NBA PLAYER1_ID: `2202`
- lineup: `[133, 248, 278, 1502, 1714, 2042, 2202, 2211, 2240, 2412]`
- real rebound: `true`
- method: `unique_player_counter_identity_with_source_clock_displacement_adjudicated`

## Candidate implementation contract

The candidate must remain isolated from production `production_rebound_v4.py` until the canary passes.

It must delegate to production v4 first, then apply exactly one repair only when `game_id == 20200077`.

Runtime assertions must fail hard unless all of the following are true:
1. Exactly one PBP rebound row matches period + start_time + end_time + description.
2. That PBP row is still unmatched by production v4.
3. Exactly one NBA row matches period + eventnum.
4. The NBA row has `EVENTMSGTYPE == 4`.
5. The NBA event is not already consumed by production v4.
6. NBA elapsed equals `773`.
7. NBA source clock equals `11:07`.
8. NBA normalized description equals `Richardson REBOUND (Off:1 Def:1)`.
9. NBA PLAYER1_ID equals `2202`.
10. Reconstructed lineup exactly equals `[133, 248, 278, 1502, 1714, 2042, 2202, 2211, 2240, 2412]`.
11. `core._nba_real_rebound(...)` is `true`.
12. After insertion, unmatched rebound-bearing rows for game `20200077` equal zero.

No matcher widening, percentage inference, rounded backsolve, source substitution, or generic clock relaxation is permitted.

## Canary scope

Use exactly the same deterministic first 50 ascending `2002-03` strict historical recovery games from:

`team_trb_all_players/final_integrity_rebuild/final_exact_release/residual_game_scope/historical_gap_partition/HISTORICAL_STRICT_RECOVERY_GAMES.csv`

Authoritative source stack remains:
- `nbastats_2002`
- `nbastatsv3_2002`
- `pbpstats_2002`

Collector remains:
`team_trb_all_players/build_exact_game_fact_layer.py::build_game`

## Required PASS contract

The canary passes only if:
- requested games = `50`
- successful games = `41`
- adjudicated repair applications = exactly `1`
- adjudicated repair game = exactly `20200077`
- failed games = exactly the same nine known non-unique lineup cases:
  - `20200169`
  - `20200200`
  - `20200261`
  - `20200344`
  - `20200359`
  - `20200373`
  - `20200464`
  - `20200619`
  - `20200839`
- every one of those nine still fails specifically as `non-unique v3/team-local starter solution`
- unexpected failures = `0`
- duplicate successful game IDs = `0`
- duplicate team exact keys = `0`
- duplicate player exact keys = `0`
- exactly two team rows per successful game
- all raw second/rebound components are non-negative integers
- no team/player row exists outside the pinned 50-game scope
- existing validated games recomputed = `0`
- safe-lock keys touched = `0`
- rounded percentage backsolve = `false`

Persist canary outputs to an isolated recovery branch only. Do not promote the repair into production until this contract passes.

## Current blocker

The connected GitHub contents/Git-object write path accepted the evidence JSON but rejected executable Python/YAML writes with the message that OpenAI could not determine the safety status of the request. This is a tooling restriction, not a project/data integrity failure.
