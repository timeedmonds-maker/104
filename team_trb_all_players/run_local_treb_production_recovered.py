#!/usr/bin/env python3
"""Targeted TREB production entry point using audited legacy recovery.

Rebound-row matching remains strict. This entry point enables only the
explicit evidence-based lineup/substitution recovery layer; any unmatched
rebound-bearing source row remains a hard production failure.
"""
from __future__ import annotations

import production_treb_engine_recovered as recovered
import run_local_treb_production as runner

# The original runner imported the locked production module at import time.
# Replace only these three callables for this targeted repair entry point.
# IMPORTANT: use the strict recovered joiner, not the historical bounded
# unmatched-rebound tolerance. Final production must not accept unmatched
# rebound-bearing rows.
runner.reconstruct_game_lineups = recovered.reconstruct_game_lineups
runner.join_pbp_rebounds = recovered.join_pbp_rebounds
runner.classify_rebounds = recovered.classify_rebounds


if __name__ == "__main__":
    raise SystemExit(runner.main())
