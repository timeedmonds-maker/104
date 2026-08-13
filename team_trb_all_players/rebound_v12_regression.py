#!/usr/bin/env python3
"""Run established source-only audit against production V12 (Sabonis promotion on V9)."""
from __future__ import annotations
import rebound_v5_source_only_audit as audit
import production_rebound_v12 as rebound_v12

audit.rebound = rebound_v12

if __name__ == '__main__':
    raise SystemExit(audit.main())
