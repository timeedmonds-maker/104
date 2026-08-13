#!/usr/bin/env python3
"""Run established source-only audit against production V13 under test."""
from __future__ import annotations
import rebound_v5_source_only_audit as audit
import production_rebound_v13 as rebound_v13

audit.rebound = rebound_v13

if __name__ == '__main__':
    raise SystemExit(audit.main())
