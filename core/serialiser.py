"""
=============================================================================
Module:        Product Serialiser
Location:      core/serialiser.py
Description:   Converts a raw gtin_cache.db row dict (as returned by
               sqlite_store.get_product()) to a JSON-safe dict ready for
               jsonify() or SSE emission.

               Called from app.py at two points:
                 - Line ~97: the "complete" jsonify path
                 - Line ~109: the "enriched" SSE path

Encoding rules (source: trigzi-contracts/contracts/gtin-cache-db.md):
  canonicals / categories  raw blobs → omitted; use pre-decoded *_list fields
  allergen                 14-bit int → list[str]  (decoded via eu14_bitmask.py)
  sources                  4-bit int  → list[str]  (woolworths/coles/iga/off)
  protein/fat/fat_sat/
    carbs/sugars/fibre     integer × 10 → float grams  (divide by 10)
  healthstar               integer × 2  → float stars  (divide by 2, range 0.5–5.0)
  nutriscore               integer 0–4  → str grade    ("A"–"E")
  nova/energy_kj/sodium/
    egl/fodmap             pass-through as int
  country                  integer FK   → omit (country_code string already present via JOIN)
  _source                  internal     → omit
=============================================================================
"""

from __future__ import annotations

import os
import sys

# ── Locate trigzi-contracts/build/ relative to this file ────────────────────
#
# Pattern mirrors write_2_schema.py's trigzi-common path resolution.
# TRIGZI_CONTRACTS_PATH env var overrides the default for non-standard layouts.
#
_here            = os.path.dirname(os.path.abspath(__file__))
_contracts_build = os.environ.get(
    "TRIGZI_CONTRACTS_PATH",
    os.path.normpath(os.path.join(_here, "..", "..", "trigzi-contracts", "build")),
)
if os.path.isdir(_contracts_build) and _contracts_build not in sys.path:
    sys.path.insert(0, _contracts_build)

import eu14_bitmask as _eu14  # noqa: E402 — path must be set first

# ── EU14 decode table: built from eu14_bitmask constants ─────────────────────
#
# Display name rule (from eu14_bitmask.h header comment):
#   "Token names are canonical — display name is token with _ -> space, title-cased."
#
# Sorted by bit value (bit 0 first) so the decoded list is always alphabetical.
_EU14_DECODE: list[tuple[int, str]] = sorted(
    [
        (val, attr[len("TRIGZI_ALLERGEN_"):].replace("_", " ").title())
        for attr, val in vars(_eu14).items()
        if attr.startswith("TRIGZI_ALLERGEN_") and isinstance(val, int)
    ],
    key=lambda x: x[0],
)

# ── Sources bitmask (SRC_* constants from trigzi-common/include/scores.h) ────
_SOURCE_NAMES: list[str] = ["woolworths", "coles", "iga", "off"]  # bit 0 … bit 3

# ── NutriScore integer 0–4 → grade string A–E ────────────────────────────────
_NUTRISCORE_GRADE: list[str] = ["A", "B", "C", "D", "E"]

# ── Fields to omit from the API response ─────────────────────────────────────
_OMIT: frozenset[str] = frozenset({
    "country",      # integer FK — country_code string is already present via JOIN
    "_source",      # internal pipeline field
    "canonicals",   # raw blob — use canonicals_list instead
    "categories",   # raw blob — use categories_list instead
})

# ── Nutrition fields stored as integer × 10; served as float grams ───────────
_NUTRITION_DIV10: frozenset[str] = frozenset({
    "protein", "fat", "fat_sat", "carbs", "sugars", "fibre",
})


# ── Public API ────────────────────────────────────────────────────────────────

def serialise_product(record: dict) -> dict:
    """
    Convert a raw gtin_cache.db row dict to a JSON-safe dict.

    Input:  dict from sqlite_store.get_product() — may contain bytes blobs,
            bitmask integers, and scaled nutrition integers.
    Output: dict ready for flask/quart jsonify() — all values are JSON-native
            (str, int, float, list, None).

    The caller should not touch the raw 'canonicals'/'categories' blobs;
    sqlite_store already decodes them into 'canonicals_list'/'categories_list'.
    """
    out: dict = {}

    for key, val in record.items():

        # ── Omit internal / raw-blob fields ──────────────────────────────────
        if key in _OMIT:
            continue

        # ── Allergen bitmask → list[str] ─────────────────────────────────────
        if key == "allergen":
            out[key] = _decode_allergen(val)
            continue

        # ── Sources bitmask → list[str] ──────────────────────────────────────
        if key == "sources":
            out[key] = _decode_sources(val)
            continue

        # ── Nutrition: integer × 10 → float grams ────────────────────────────
        if key in _NUTRITION_DIV10:
            out[key] = round(val / 10.0, 1) if val is not None else None
            continue

        # ── HealthStar: integer × 2 → float stars ────────────────────────────
        if key == "healthstar":
            out[key] = round(val / 2.0, 1) if val is not None else None
            continue

        # ── NutriScore: integer 0–4 → grade string ───────────────────────────
        if key == "nutriscore":
            if val is not None and 0 <= val <= 4:
                out[key] = _NUTRISCORE_GRADE[val]
            else:
                out[key] = None
            continue

        # ── Pass-through (nova, energy_kj, sodium, egl, fodmap, etc.) ────────
        out[key] = val

    return out


# ── Private helpers ───────────────────────────────────────────────────────────

def _decode_allergen(mask: int | None) -> list[str]:
    """Decode a 14-bit EU14 allergen bitmask to a list of display-name strings.

    Bit positions and display names are derived from eu14_bitmask constants
    loaded from trigzi-contracts/build/eu14_bitmask.py at import time.
    """
    if not mask:
        return []
    return [name for val, name in _EU14_DECODE if mask & val]


def _decode_sources(mask: int | None) -> list[str]:
    """Decode a 4-bit sources bitmask to a list of source name strings."""
    if not mask:
        return []
    return [name for bit, name in enumerate(_SOURCE_NAMES) if mask & (1 << bit)]
