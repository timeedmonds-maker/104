from __future__ import annotations

import enrich_transactions_realgm_resilient_v3 as v3

# Extend the already-validated partial RealGM enrichment across every TREB
# season.  v3 keeps per-season validated caches, records unavailable pages,
# and never bypasses downstream strict overlap QA.
v3.YEARS = list(range(2000, 2026))

if __name__ == "__main__":
    raise SystemExit(v3.main())
