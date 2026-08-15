#!/usr/bin/env python3
from __future__ import annotations

import multiprocessing as mp
import queue
import residual_materiality_autopilot as m

TIMEOUT_SECONDS = 300
_orig = m.game_variants


def _worker(outq, gid, ng, vg, pg, candidate_map):
    try:
        outq.put(('OK', _orig(gid, ng, vg, pg, candidate_map)))
    except BaseException as exc:
        outq.put(('ERROR', {'type': type(exc).__name__, 'error': str(exc)}))


def guarded_game_variants(gid, ng, vg, pg, candidate_map):
    # A Python SIGALRM cannot reliably interrupt long native/C-extension work.
    # Use a forked process so a pathological game can be terminated hard while
    # preserving the caller's in-memory monkeypatches and all integrity gates.
    ctx = mp.get_context('fork')
    outq = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_worker, args=(outq, gid, ng, vg, pg, candidate_map))
    proc.start()
    proc.join(TIMEOUT_SECONDS)
    if proc.is_alive():
        proc.terminate()
        proc.join(10)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        return [], {
            'status': 'GAME_COMPUTE_TIMEOUT',
            'timeout_seconds': TIMEOUT_SECONDS,
            'variants': 0,
            'lineup_capped': False,
            'rebound_capped': False,
            'hard_process_timeout': True,
        }
    try:
        kind, payload = outq.get_nowait()
    except queue.Empty:
        return [], {
            'status': 'GAME_COMPUTE_PROCESS_ERROR',
            'exitcode': proc.exitcode,
            'variants': 0,
            'lineup_capped': False,
            'rebound_capped': False,
        }
    finally:
        outq.close()
    if kind == 'OK':
        return payload
    return [], {
        'status': 'GAME_COMPUTE_ERROR',
        'error_type': payload.get('type', ''),
        'error': payload.get('error', ''),
        'variants': 0,
        'lineup_capped': False,
        'rebound_capped': False,
    }


m.game_variants = guarded_game_variants

if __name__ == '__main__':
    m.main()
