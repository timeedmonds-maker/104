#!/usr/bin/env python3
"""Direct exact recovery using only rebound-event exclusions uniquely forced by retained exact integer controls.

The three exclusions below are not semantic guesses: the existing exact event-constraint
solver found each event uniquely necessary to reconcile overlapping exact player/team
controls. Everything else uses the unchanged fail-closed target-player recovery engine.
This path is production-targeted: any zero-mismatch candidates are intended for immediate
conflict-check, authoritative union, and affected-key reclosure.
"""
from __future__ import annotations

import treb_target_player_interval_recovery as recovery

FORCED_NOT_REAL = {
    (20400493, 222),
    (20400526, 298),
    (21901285, 158),
}

_original = recovery.core._nba_real_rebound


def exact_real_rebound(game, idx):
    try:
        game_id = int(float(game.loc[idx, 'GAME_ID'])) if 'GAME_ID' in game.columns else int(float(game.GAME_ID.iloc[0]))
        eventnum = int(float(game.loc[idx, 'EVENTNUM']))
    except Exception:
        return _original(game, idx)
    if (game_id, eventnum) in FORCED_NOT_REAL:
        return False
    return _original(game, idx)


recovery.core._nba_real_rebound = exact_real_rebound

if __name__ == '__main__':
    raise SystemExit(recovery.main())
