#!/usr/bin/env python3
"""Run established source-only audit against production V8 (V8b promotion)."""
from __future__ import annotations
import rebound_v5_source_only_audit as audit
import production_rebound_v8 as rebound_v8

audit.rebound = rebound_v8

if __name__ == '__main__':
    raise SystemExit(audit.main())
