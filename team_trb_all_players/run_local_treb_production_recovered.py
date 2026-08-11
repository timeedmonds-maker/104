#!/usr/bin/env python3
"""Targeted TREB production entry point using audited legacy recovery."""
from __future__ import annotations

import production_treb_engine_tolerant as recovered
import run_local_treb_production as runner

# The original runner imported the locked production module at import time.
# Replace only these three callables for this targeted repair entry point.
runner.reconstruct_game_lineups = recovered.reconstruct_game_lineups
runner.join_pbp_rebounds = recovered.join_pbp_rebounds
runner.classify_rebounds = recovered.classify_rebounds


if __name__ == "__main__":
    raise SystemExit(runner.main())
