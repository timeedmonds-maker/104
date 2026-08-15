#!/usr/bin/env python3
from __future__ import annotations
import signal
import residual_materiality_autopilot as m

TIMEOUT_SECONDS = 300
_orig = m.game_variants

class GameVariantTimeout(Exception):
    pass

def _handler(signum, frame):
    raise GameVariantTimeout()

def guarded_game_variants(gid, ng, vg, pg, candidate_map):
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        return _orig(gid, ng, vg, pg, candidate_map)
    except GameVariantTimeout:
        return [], {
            'status': 'GAME_COMPUTE_TIMEOUT',
            'timeout_seconds': TIMEOUT_SECONDS,
            'variants': 0,
            'lineup_capped': False,
            'rebound_capped': False,
        }
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

m.game_variants = guarded_game_variants

if __name__ == '__main__':
    m.main()
