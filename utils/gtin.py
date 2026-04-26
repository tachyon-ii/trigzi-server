#!/usr/bin/env python3
"""
=============================================================================
Module:        GTIN Normalisation
Location:      utils/gtin.py
Description:   GTIN normalisation following the Open Food Facts barcode
               specification. Coerces every barcode variant the scanners
               might emit (EAN-8, UPC-A, UPC-E, EAN-13, EAN-14) into the
               canonical 13-digit EAN-13 form, or returns None for any
               input that's invalid or non-consumer.

Architecture Note:
The algorithm:
  - Strip leading zeros to get numeric value
  - < 13 digits → zfill(13)   — covers EAN-8, UPC-A, UPC-E etc.
  - 13 digits   → as-is       — EAN-13 canonical
  - 14 digits starting with 0 → strip leading 0 → EAN-13
  - 14 digits starting with non-0 → None (genuine EAN-14, non-consumer)
  - > 14 digits → None (invalid/placeholder)
  - No valid digits → None

Reference: https://wiki.openfoodfacts.org/Barcode_normalization
=============================================================================
"""

from __future__ import annotations

from typing import List, Optional


def normalise(gtin: str) -> Optional[str]:
    """
    Normalise a GTIN to its canonical EAN-13 form.
    Returns None if the GTIN is invalid or non-consumer.
    """
    if not gtin or not isinstance(gtin, str):
        return None

    gtin = gtin.strip()
    if not gtin.isdigit():
        return None

    n = len(gtin)

    if n > 14:
        return None

    if n == 14:
        if gtin[0] == '0':
            return gtin[1:]   # trim leading 0 → 13 digits
        return None            # genuine EAN-14, non-consumer

    # n <= 13: pad to 13
    return gtin.zfill(13)


def variations(gtin: str) -> List[str]:
    """
    Return lookup candidates for a scanned GTIN.
    Always returns at most one candidate — the normalised form.
    """
    canonical = normalise(gtin)
    if not canonical:
        return []
    return [canonical]
