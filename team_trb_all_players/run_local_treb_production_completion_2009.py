#!/usr/bin/env python3
"""2009-only supplement for the final TREB completion pass.

The first completion run exposed one additional exact substitution-sequence
anomaly in game 20900113 (event 475).  It is excluded explicitly here, rather
than weakening the general engine.  All other completion rules remain
unchanged.
"""
from __future__ import annotations

import run_local_treb_production_completion as completion

SUPPLEMENTAL_GAME = 20900113
completion.ACCEPTED_COMPLETION_EXCEPTIONS[SUPPLEMENTAL_GAME] = "sub_out_absent_completion_run_31547730942_event_475"

_original_accepted_record = completion.accepted_record

def _accepted_record(gid: int) -> dict:
    if int(gid) == SUPPLEMENTAL_GAME:
        return {
            "game_id": SUPPLEMENTAL_GAME,
            "type": "accepted_completion_game_exclusion",
            "diagnostic_signature": "sub_out_absent_completion_run_31547730942_event_475",
            "source": "completion_run_31547730942_job_93964115288",
            "resolution": "excluded_from_player_on_off_accumulation",
            "scope": "single_supplemental_game_only",
        }
    return _original_accepted_record(gid)

completion.accepted_record = _accepted_record

if __name__ == "__main__":
    raise SystemExit(completion.main())
