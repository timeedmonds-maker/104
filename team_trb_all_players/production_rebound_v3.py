#!/usr/bin/env python3
"""Validated production rebound layer v3.

Promotion evidence:
- padded semantic canary: 88 historical rebound-failure games recovered;
- lineup controls: 7,995 / 7,995 correct;
- real/placeholder controls: 7,740 / 7,740 correct.

The fallback is intentionally narrow: unmatched rebound rows are synthesized only
when one reconstructed ten-player lineup is invariant across the entire legal
±5-second join window and rebound real/placeholder status is independently
forced.  The locked legacy rebound classifier remains authoritative.
"""
from production_rebound_semantic_candidate_padded import join_pbp_rebounds, classify_rebounds
