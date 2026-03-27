#!/usr/bin/env python3
from __future__ import annotations
#
#  core/data_manager.py
#
#  Thin product lookup layer. Read-only access to the products table.
#  All write operations (enrichment, analysis) are in enricher.py and analyser.py.
#
#  Cases:
#    Case 1: Record in DB, enrichment_id set   → return immediately (enriched)
#    Case 2: Record in DB, enrichment_id null  → caller streams enrichment
#    Case 3: Not in DB                         → return None → 404
#
#  Debug flags:
#    DEBUG_FORCE_NOT_FOUND=1   — always return None
#    DEBUG_FORCE_UNENRICHED=1  — strip enrichment_id, simulate Case 2
#

import os
from typing import Optional

from utils.off_lookup import OFFLookup
from utils.gtin import normalise

DEBUG_FORCE_NOT_FOUND  = os.environ.get("DEBUG_FORCE_NOT_FOUND",  "0") == "1"
DEBUG_FORCE_UNENRICHED = os.environ.get("DEBUG_FORCE_UNENRICHED", "0") == "1"

_off = OFFLookup()


def get_product(scanned_gtin: str) -> Optional[dict]:
    if DEBUG_FORCE_NOT_FOUND:
        print(f"  [DEBUG] FORCE_NOT_FOUND: {scanned_gtin}")
        return None

    gtin = normalise(scanned_gtin)
    if not gtin:
        return None

    record = _off.get(gtin)
    if not record:
        return None

    if DEBUG_FORCE_UNENRICHED:
        record = dict(record)
        record["_enrichment_llm"] = None
        record["clinical_profile"] = None

    return record


def is_enriched(record: dict) -> bool:
    return bool(record.get("_enrichment_llm"))
