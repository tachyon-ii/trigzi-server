#!/usr/bin/env python3
"""
=============================================================================
Module:        GTIN Normalisation
Location:      utils/gtin.py
Description:   GTIN normalisation following the Open Food Facts barcode
               specification. Coerces every barcode variant the scanners
               might emit (EAN-8, UPC-A, UPC-E, EAN-13, EAN-14) into the
               canonical 13-digit EAN-13 form, or returns None for any
               input that is structurally unsalvageable.

Architecture Note:
normalise() is structural coercion only — it does NOT validate the GS1
check digit. Reasons:

  1. The database is pre-cleaned: every GTIN in the products table has
     already been validated and any failures deleted (see test_gs1.py /
     delete_invalid_gtins.sql). Revalidating on the read path adds no
     safety.

  2. Real scanners can produce slightly mangled reads (transposition,
     worn barcodes). Rejecting these at normalisation creates false
     negatives — the lookup would have found the product anyway.

  3. The check digit is meaningful for data entry and export pipelines,
     not for lookup.

is_valid_gs1() is kept for use in data-quality tooling (test_gs1.py,
import pipelines) where it belongs.

The normalisation algorithm:
  - Strip whitespace; reject non-numeric and empty inputs.
  - > 14 digits → None (invalid/placeholder)
  - 14 digits, leading 0 → strip leading 0 → 13-digit candidate
  - 14 digits, non-zero leader → None (genuine EAN-14, non-consumer)
  - <= 13 digits → zfill(13)

Reference: https://wiki.openfoodfacts.org/Barcode_normalization
=============================================================================
"""

from __future__ import annotations

from typing import List, Optional


def is_valid_gs1(gtin: str) -> bool:
    """Validate a GTIN against the GS1 check digit algorithm.

    For use in data-quality pipelines and export validation — NOT on the
    product lookup read path. See module docstring.

    GS1 barcodes (EAN-8, EAN-13, UPC-A, ITF-14) use alternating weights
    of 1 and 3. The check digit is the last digit; it makes the weighted
    sum divisible by 10.
    """
    if not gtin.isdigit() or len(gtin) < 2:
        return False
    total = 0
    for i, digit in enumerate(reversed(gtin[:-1])):
        weight = 3 if i % 2 == 0 else 1
        total += int(digit) * weight
    check = (10 - (total % 10)) % 10
    return check == int(gtin[-1])


def normalise(gtin: str) -> Optional[str]:
    """Coerce a scanned GTIN to its canonical 13-digit form.

    Returns None only for structurally unsalvageable input: non-numeric,
    empty, over 14 digits, or a genuine (non-zero-leading) EAN-14.
    Does NOT apply GS1 check digit validation — see module docstring.
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
            return gtin[1:]    # strip leading 0 → 13 digits
        return None            # genuine EAN-14, non-consumer

    # n <= 13: zero-pad to 13
    return gtin.zfill(13)


def variations(gtin: str) -> List[str]:
    """Return lookup candidates for a scanned GTIN.

    Always returns at most one candidate — the normalised form.
    """
    canonical = normalise(gtin)
    if not canonical:
        return []
    return [canonical]
