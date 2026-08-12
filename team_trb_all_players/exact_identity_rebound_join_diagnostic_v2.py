#!/usr/bin/env python3
"""Compatibility wrapper for exact_identity_rebound_join_diagnostic.

The first full-coverage diagnostic correctly identified exact fallback matches,
but audit logging expected a raw DESCRIPTION column on reconstructed NBA event
snapshots. The production lineup engine exposes DESCRIPTION_NORM instead. This
wrapper adds a diagnostic-only DESCRIPTION alias before invoking the unchanged
diagnostic. It does not alter lineup reconstruction, rebound matching, or any
production engine logic.
"""
from __future__ import annotations

import exact_identity_rebound_join_diagnostic as diagnostic
import production_treb_engine_v3 as lineup_engine

_original_reconstruct = lineup_engine.reconstruct_game_lineups


def _reconstruct_with_audit_description(*args, **kwargs):
    result = _original_reconstruct(*args, **kwargs)
    if "DESCRIPTION" not in result.events.columns and "DESCRIPTION_NORM" in result.events.columns:
        result.events = result.events.copy()
        result.events["DESCRIPTION"] = result.events["DESCRIPTION_NORM"]
    return result


lineup_engine.reconstruct_game_lineups = _reconstruct_with_audit_description

if __name__ == "__main__":
    raise SystemExit(diagnostic.main())
