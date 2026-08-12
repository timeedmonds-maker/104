#!/usr/bin/env python3
"""Run the established V5 source-only audit against the production V6 join layer."""
from __future__ import annotations
import rebound_v5_source_only_audit as audit
import production_rebound_v6 as rebound_v6

audit.rebound = rebound_v6

if __name__ == '__main__':
    raise SystemExit(audit.main())
