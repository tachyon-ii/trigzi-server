#!/usr/bin/env python3
"""
=============================================================================
Module:        Data Manager
Location:      core/data_manager.py
Description:   Thin product lookup layer. Read-only access to the products
               table via the OFFLookup wrapper around Open Food Facts.
               All write operations (enrichment, analysis) live elsewhere
               — see enricher.py and analyser.py for the streaming and
               LLM paths respectively.

Architecture Note:
get_product() returns one of three shapes that the route layer must
distinguish:

    Case 1: Record in DB, enrichment_id set   → return immediately (enriched)
    Case 2: Record in DB, enrichment_id null  → caller streams enrichment
    Case 3: Not in DB                         → return None → 404

Debug flags (env vars, both default off):
    DEBUG_FORCE_NOT_FOUND=1   — always return None (forces Case 3)
    DEBUG_FORCE_UNENRICHED=1  — strip enrichment_id, simulate Case 2
=============================================================================
"""

from __future__ import annotations

import os
from typing import Optional

from utils.off_lookup import OFFLookup
from utils.gtin import normalise

DEBUG_FORCE_NOT_FOUND  = os.environ.get("DEBUG_FORCE_NOT_FOUND",  "0") == "1"
DEBUG_FORCE_UNENRICHED = os.environ.get("DEBUG_FORCE_UNENRICHED", "0") == "1"

_off = OFFLookup()


async def get_product(scanned_gtin: str) -> Optional[dict]:
    """Look up a product by scanned GTIN. Returns the OFF record dict, or None for Case 3.

    The returned record may or may not have ``_enrichment_llm`` set — the
    caller decides whether to stream enrichment based on that flag. Honors
    the two DEBUG_* environment variables for testing the route layer's
    handling of each of the three cases.
    """
    if DEBUG_FORCE_NOT_FOUND:
        print(f"  [DEBUG] FORCE_NOT_FOUND: {scanned_gtin}")
        return None

    gtin = normalise(scanned_gtin)
    if not gtin:
        return None

    record = await _off.get(gtin)
    if not record:
        return None

    if DEBUG_FORCE_UNENRICHED:
        record = dict(record)
        record["_enrichment_llm"] = None
        record["clinical_profile"] = None

    return record


def is_enriched(record: dict) -> bool:
    """True if the product record has been enriched by an LLM analysis pass."""
    return bool(record.get("_enrichment_llm"))
