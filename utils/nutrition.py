#!/usr/bin/env python3
"""
=============================================================================
Module:        Nutrition Normaliser
Location:      utils/nutrition.py
Description:   Normalises nutrition data from any source (Woolworths SAP
               JSON, Coles structured breakdown, Open Food Facts dump)
               into the unified NutriObject schema. Handles the
               source-specific quirks (kJ-vs-kcal, per-100g-vs-per-serve,
               unit suffixes like "21.8 g" / "<1 g") in one place.

NutriObject schema:
{
    "energy_kj":       float | null,
    "calories_kcal":   float | null,   # derived: round(kj / 4.184, 1)
    "protein_g":       float | null,
    "fat_total_g":     float | null,
    "fat_saturated_g": float | null,
    "carbohydrates_g": float | null,
    "sugars_g":        float | null,
    "fibre_g":         float | null,
    "sodium_mg":       float | null,
}

Three null/zero conventions matter:
  null  = not declared on panel
  0.0   = declared zero (or "<1" trace coerced down)
  {}    = no nutrition panel at all (non-food product)

Architecture Note:
Each provider has its own parse_* entry point that returns the same
4-tuple: (macros_100g, macros_serve, serving_size_g, servings_per_pack).
The internal _build_nutri / _extract_float / _kcal helpers are shared
across providers so source-specific code stays focused on the upstream
shape, not the canonical output.
=============================================================================
"""

import json
import re
from typing import Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _extract_float(raw) -> Optional[float]:
    """Extract a float from a value that may be a string like '21.8 g', '150 mg', '<1'."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ('-', 'N/A', 'n/a'):
        return None
    if s.startswith('<'):
        # "<1 g" → treat as 0.0 (declared trace)
        return 0.0
    # Strip everything except digits and decimal point
    clean = re.sub(r'[^\d.]', '', s)
    if not clean:
        return None
    try:
        return float(clean)
    except ValueError:
        return None


def _kcal(kj: Optional[float]) -> Optional[float]:
    """Convert kilojoules to kilocalories using the standard 4.184 ratio."""
    if kj is None:
        return None
    return round(kj / 4.184, 1)


def _empty_nutri() -> dict:
    """Return the empty NutriObject ({}) sentinel for non-food / no-panel records."""
    return {}


def _build_nutri(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    energy_kj=None,
    protein_g=None,
    fat_total_g=None,
    fat_saturated_g=None,
    carbohydrates_g=None,
    sugars_g=None,
    fibre_g=None,
    sodium_mg=None,
) -> dict:
    """Build a NutriObject, omitting keys that are null.

    The argument count (8) exceeds pylint's default of 5, but the
    function is genuinely one-argument-per-nutrient — splitting into
    sub-builders or a dict-input pattern would lose the keyword-arg
    self-documenting quality at every call site. The shape mirrors the
    NutriObject schema described in the module docstring.
    """
    obj = {}
    if energy_kj is not None:
        obj["energy_kj"]       = energy_kj
        obj["calories_kcal"]   = _kcal(energy_kj)
    if protein_g is not None:
        obj["protein_g"]       = protein_g
    if fat_total_g is not None:
        obj["fat_total_g"]     = fat_total_g
    if fat_saturated_g is not None:
        obj["fat_saturated_g"] = fat_saturated_g
    if carbohydrates_g is not None:
        obj["carbohydrates_g"] = carbohydrates_g
    if sugars_g is not None:
        obj["sugars_g"]        = sugars_g
    if fibre_g is not None:
        obj["fibre_g"]         = fibre_g
    if sodium_mg is not None:
        obj["sodium_mg"]       = sodium_mg
    return obj


# ---------------------------------------------------------------------------
# Woolworths
# Nutrition lives in AdditionalAttributes.nutritionalinformation (JSON string)
# Keys like "Energy kJ Quantity Per 100g - Total - NIP"
# ---------------------------------------------------------------------------

_WW_100G = {
    "Energy kJ Quantity Per 100g - Total - NIP":           "energy_kj",
    "Protein Quantity Per 100g - Total - NIP":             "protein_g",
    "Fat Total Quantity Per 100g - Total - NIP":           "fat_total_g",
    "Fat Saturated Quantity Per 100g - Total - NIP":       "fat_saturated_g",
    "Carbohydrate Quantity Per 100g - Total - NIP":        "carbohydrates_g",
    "Sugars Quantity Per 100g - Total - NIP":              "sugars_g",
    "Dietary Fibre Quantity Per 100g - Total - NIP":       "fibre_g",
    "Sodium Quantity Per 100g - Total - NIP":              "sodium_mg",
}

_WW_SERVE = {
    "Energy kJ Quantity Per Serve - Total - NIP":          "energy_kj",
    "Protein Quantity Per Serve - Total - NIP":            "protein_g",
    "Fat Total Quantity Per Serve - Total - NIP":          "fat_total_g",
    "Fat Saturated Quantity Per Serve - Total - NIP":      "fat_saturated_g",
    "Carbohydrate Quantity Per Serve - Total - NIP":       "carbohydrates_g",
    "Sugars Quantity Per Serve - Total - NIP":             "sugars_g",
    "Dietary Fibre Quantity Per Serve - Total - NIP":      "fibre_g",
    "Sodium Quantity Per Serve - Total - NIP":             "sodium_mg",
}


def parse_woolworths(raw_nip: Optional[str]) -> Tuple[dict, dict, Optional[float], Optional[float]]:
    """
    Parse Woolworths AdditionalAttributes.nutritionalinformation JSON string.
    Returns (macros_100g, macros_serve, serving_size_g, servings_per_package).
    """
    if not raw_nip:
        return _empty_nutri(), _empty_nutri(), None, None

    try:
        nip = json.loads(raw_nip)
    except (json.JSONDecodeError, TypeError):
        return _empty_nutri(), _empty_nutri(), None, None

    vals_100g = {}
    vals_serve = {}
    serving_size_g = None
    servings_per_package = None

    for attr in nip.get("Attributes", []):
        key = attr.get("Name", "")
        val = attr.get("Value")

        if key == "Serving Size - Total - NIP":
            serving_size_g = _extract_float(val)
        elif key == "Servings Per Pack - Total - NIP":
            servings_per_package = _extract_float(val)
        elif key in _WW_100G:
            v = _extract_float(val)
            if v is not None:
                vals_100g[_WW_100G[key]] = v
        elif key in _WW_SERVE:
            v = _extract_float(val)
            if v is not None:
                vals_serve[_WW_SERVE[key]] = v

    macros_100g = _build_nutri(**vals_100g) if vals_100g else _empty_nutri()
    macros_serve = _build_nutri(**vals_serve) if vals_serve else _empty_nutri()

    return macros_100g, macros_serve, serving_size_g, servings_per_package


# ---------------------------------------------------------------------------
# Coles
# nutrition_json is a string containing:
# {"breakdown": [{"title": "Per Serving", "nutrients": [...]},
#                {"title": "Per 100g/ml", "nutrients": [...]}],
#  "servingSize": "115g", "servingsPerPackage": "23.00"}
# ---------------------------------------------------------------------------

def _map_coles_nutrient(name: str) -> Optional[str]:  # pylint: disable=too-many-return-statements
    """Map a Coles nutrient label to its canonical NutriObject key.

    Returns one canonical key per Coles-side label match. The 9 returns
    mirror the 9 NutriObject fields — collapsing them to a dict-lookup
    would obscure the substring/heuristic logic each branch encodes
    (e.g. "carbohydrate AND NOT sugar" disambiguating
    "Carbohydrate excluding sugars" from "Sugars" rows).
    """
    n = name.lower()
    if "energy" in n and ("kj" in n or n == "energy"):
        return "energy_kj"
    if n.startswith("protein"):
        return "protein_g"
    if "fat" in n and "total" in n:
        return "fat_total_g"
    if "saturated" in n:
        return "fat_saturated_g"
    if "carbohydrate" in n and "sugar" not in n:
        return "carbohydrates_g"
    if "sugar" in n:
        return "sugars_g"
    if "fibre" in n or "fiber" in n:
        return "fibre_g"
    if "sodium" in n:
        return "sodium_mg"
    return None


def parse_coles(raw: Union[str, dict, None]) -> Tuple[dict, dict, Optional[float], Optional[float]]:
    """
    Parse Coles nutrition_json.
    Returns (macros_100g, macros_serve, serving_size_g, servings_per_package).
    """
    if not raw:
        return _empty_nutri(), _empty_nutri(), None, None

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return _empty_nutri(), _empty_nutri(), None, None
    else:
        data = raw

    if not data:
        return _empty_nutri(), _empty_nutri(), None, None

    serving_size_g       = _extract_float(data.get("servingSize"))
    servings_per_package = _extract_float(data.get("servingsPerPackage"))

    vals_100g  = {}
    vals_serve = {}

    for section in data.get("breakdown", []):
        title = (section.get("title") or "").lower()
        if "100" in title:
            target = vals_100g
        elif "serv" in title:
            target = vals_serve
        else:
            continue

        for n in section.get("nutrients", []):
            key = _map_coles_nutrient(n.get("nutrient", ""))
            if key:
                v = _extract_float(n.get("value"))
                if v is not None:
                    target[key] = v

    macros_100g  = _build_nutri(**vals_100g)  if vals_100g  else _empty_nutri()
    macros_serve = _build_nutri(**vals_serve) if vals_serve else _empty_nutri()

    return macros_100g, macros_serve, serving_size_g, servings_per_package
