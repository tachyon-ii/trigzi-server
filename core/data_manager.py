#!/usr/bin/env python3
"""
=============================================================================
Module:        Data Manager
Location:      core/data_manager.py
Description:   Product lookup layer. Queries the three OTA SQLite files via
               sqlite_store, with gtin_miss_cache (MariaDB) as a fallback
               for GTINs not yet in gtin_cache.db.

Lookup priority:
    1. gtin_cache.db  (sqlite_store) — pre-validated, O(1), the norm
    2. gtin_miss_cache (MariaDB)     — previously enriched phone-home hits
    3. None → 404 → caller initiates on-demand enrichment path

The enrichment path (OCR → LLM → result) is handled by enricher.py and
stored back into gtin_miss_cache so subsequent lookups hit case 2.

Debug flags (env vars, both default off):
    DEBUG_FORCE_NOT_FOUND=1   — always return None (forces 404)
    DEBUG_FORCE_UNENRICHED=1  — simulate a gtin_miss_cache miss
=============================================================================
"""

from __future__ import annotations

import os
from typing import Optional

from core import sqlite_store
from core.db import get_pool
from utils.gtin import normalise

DEBUG_FORCE_NOT_FOUND  = os.environ.get("DEBUG_FORCE_NOT_FOUND",  "0") == "1"
DEBUG_FORCE_UNENRICHED = os.environ.get("DEBUG_FORCE_UNENRICHED", "0") == "1"


# ── Primary lookup ────────────────────────────────────────────────────────────

async def get_product(scanned_gtin: str) -> Optional[dict]:
    """
    Look up a product by scanned GTIN.

    Returns a product dict or None. The dict always has a `_source` key:
        'gtin_cache'   — from gtin_cache.db (pre-computed)
        'miss_cache'   — from gtin_miss_cache (on-demand enrichment)

    None means the product is genuinely unknown — caller should trigger
    the on-device OCR / on-demand enrichment path.
    """
    if DEBUG_FORCE_NOT_FOUND:
        return None

    gtin_str = normalise(scanned_gtin)
    if not gtin_str:
        return None

    gtin_int = int(gtin_str)

    # 1. gtin_cache.db — the fast path
    if not DEBUG_FORCE_UNENRICHED:
        record = sqlite_store.get_product(gtin_int)
        if record:
            record["_source"] = "gtin_cache"
            return record

    # 2. gtin_miss_cache — previously enriched phone-home hit
    cached = await _get_miss_cache(gtin_int)
    if cached:
        cached["_source"] = "miss_cache"
        return cached

    return None


def is_enriched(record: dict) -> bool:
    """
    True if the record came from gtin_cache.db (fully pre-computed) or has
    a clinical profile attached (gtin_miss_cache with enrichment).
    """
    source = record.get("_source", "")
    if source == "gtin_cache":
        return True
    # miss_cache records are enriched if they have a canonicals blob
    return bool(record.get("canonicals"))


# ── gtin_miss_cache ───────────────────────────────────────────────────────────

async def _get_miss_cache(gtin_int: int) -> Optional[dict]:
    """Fetch a cached miss-path enrichment result from MariaDB."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM gtin_miss_cache WHERE gtin = %s",
                    (gtin_int,)
                )
                row = await cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  [!] gtin_miss_cache lookup error: {exc}")
        return None


async def save_miss_cache(record: dict) -> bool:
    """
    Upsert an on-demand enrichment result into gtin_miss_cache.
    Called by enricher.py after enriching a phone-home miss.
    """
    gtin_int = record.get("gtin")
    if not gtin_int:
        return False
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO gtin_miss_cache
                        (gtin, country_code, name, brand, raw_ingredients,
                         allergen, canonicals, nova, nutriscore, healthstar, egl, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        name            = VALUES(name),
                        brand           = VALUES(brand),
                        raw_ingredients = VALUES(raw_ingredients),
                        allergen        = VALUES(allergen),
                        canonicals      = VALUES(canonicals),
                        nova            = VALUES(nova),
                        nutriscore      = VALUES(nutriscore),
                        healthstar      = VALUES(healthstar),
                        egl             = VALUES(egl),
                        source          = VALUES(source),
                        updated_at      = CURRENT_TIMESTAMP
                    """,
                    (
                        gtin_int,
                        record.get("country_code", "AU"),
                        record.get("name", "")[:512],
                        record.get("brand", "")[:256] if record.get("brand") else None,
                        record.get("raw_ingredients"),
                        record.get("allergen", 0),
                        record.get("canonicals"),
                        record.get("nova"),
                        record.get("nutriscore"),
                        record.get("healthstar"),
                        record.get("egl"),
                        record.get("source", "llm"),
                    ),
                )
        return True
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  [!] gtin_miss_cache save error: {exc}")
        return False
