#!/usr/bin/env python3
from __future__ import annotations
#
#  scripts/llm_pull.py
#
#  Stage 2: Parse a raw LLM response file -> validate -> pretty print.
#
#  Can be run repeatedly against the same response file to iterate
#  the parser without re-calling the LLM. Response files are the
#  regression test corpus. Works with responses from any provider --
#  model is read from the # MODEL: header written by llm_push.py.
#
#  Usage:
#      ./scripts/llm_pull.py logs/llm_responses/1743000000_9310077217814.txt
#      ./scripts/llm_pull.py logs/llm_responses/1743000000_9310077217814.txt --strict
#      ./scripts/llm_pull.py logs/llm_responses/*.txt   # batch
#

import os
import sys
import re
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# --- Field definitions ---
# (key, type, required)
FIELDS = [
    ('name',                 str,   True),
    ('brand',                str,   False),
    ('package_size',         str,   False),
    ('serving_size',         str,   False),
    ('servings_per_package', float, False),
    ('health_star_rating',   float, False),
    ('ingredients',          list,  False),
    ('energy_kj',            float, False),
    ('calories_kcal',        float, False),
    ('protein_g',            float, False),
    ('fat_total_g',          float, False),
    ('fat_saturated_g',      float, False),
    ('carbohydrates_g',      float, False),
    ('sugars_g',             float, False),
    ('fibre_g',              float, False),
    ('sodium_mg',            float, False),
    ('fodmap',               int,   True),
    ('coeliac',              int,   True),
    ('histamine',            int,   True),
    ('allergens',            list,  False),
    ('summary',              str,   True),
]

FIELD_NAMES = {f[0] for f in FIELDS}


def parse_header(text: str) -> dict:
    """Extract # KEY: value header lines written by llm_push.py."""
    meta = {}
    for line in text.split('\n'):
        if not line.startswith('#'):
            break
        if ':' in line:
            key, _, val = line[1:].partition(':')
            meta[key.strip().lower()] = val.strip()
    return meta


def parse_response(text: str) -> tuple[dict, list[str]]:
    """
    Parse key: value lines from LLM response.
    Skips comment header lines (start with #).
    Returns (parsed_dict, list_of_warnings).
    """
    parsed   = {}
    warnings = []

    for line in text.split('\n'):
        if line.startswith('#'):
            continue
        line = line.strip()
        if not line:
            continue

        if ':' not in line:
            warnings.append(f"Skipped (no colon): {line!r}")
            continue

        key, _, value = line.partition(':')
        key   = key.strip().lower().replace(' ', '_')
        value = value.strip()

        if key not in FIELD_NAMES:
            warnings.append(f"Unknown key: {key!r}")
            continue

        parsed[key] = value

    return parsed, warnings


def coerce(parsed: dict) -> tuple[dict, list[str]]:
    """
    Coerce string values to their target types.
    Returns (coerced_dict, list_of_errors).
    """
    result = {}
    errors = []

    for key, typ, required in FIELDS:
        raw = parsed.get(key, '').strip()

        if not raw:
            if required:
                errors.append(f"Missing required field: {key}")
            result[key] = None
            continue

        try:
            if typ == float:
                result[key] = float(raw)
            elif typ == int:
                result[key] = int(float(raw))
            elif typ == list:
                raw = re.sub(r'^\s*\w[\w\s]*:\s*', '', raw, count=1)
                result[key] = [i.strip() for i in raw.split(',') if i.strip()]
            else:
                result[key] = raw
        except (ValueError, TypeError) as e:
            errors.append(f"Coerce failed {key}={raw!r}: {e}")
            result[key] = None

    return result, errors


def to_schema(coerced: dict, gtin: str, model: str) -> dict:
    """Map parsed fields to unified product schema."""
    return {
        "gtin":             gtin,
        "source":           "ocr",
        "brand":            coerced.get("brand") or "",
        "name":             coerced.get("name") or "",
        "image_url":        "",
        "package_size":     coerced.get("package_size") or "",
        "category":         "",
        "subcategory":      "",
        "health_star_rating": coerced.get("health_star_rating"),
        "serving_size_g":   coerced.get("serving_size"),
        "servings_per_pack": coerced.get("servings_per_package"),
        "nutrition_100g": {
            "energy_kj":       coerced.get("energy_kj"),
            "calories_kcal":   coerced.get("calories_kcal"),
            "protein_g":       coerced.get("protein_g"),
            "fat_total_g":     coerced.get("fat_total_g"),
            "fat_saturated_g": coerced.get("fat_saturated_g"),
            "carbohydrates_g": coerced.get("carbohydrates_g"),
            "sugars_g":        coerced.get("sugars_g"),
            "fibre_g":         coerced.get("fibre_g"),
            "sodium_mg":       coerced.get("sodium_mg"),
        },
        "raw_ingredients":    ', '.join(coerced.get("ingredients") or []),
        "parsed_ingredients": coerced.get("ingredients") or [],
        "clinical_profile": {
            "estimated_health_star": None,
            "fodmap_rating":    coerced.get("fodmap"),
            "coeliac_rating":   coerced.get("coeliac"),
            "histamine_rating": coerced.get("histamine"),
            "allergen_warnings": coerced.get("allergens") or [],
            "health_summary":   coerced.get("summary") or "",
        },
        "_source_id":      gtin,
        "_source_name":    "ocr",
        "_enrichment_llm": model or "ocr_pipeline",
    }


def process_file(path: str, strict: bool) -> bool:
    """Process one response file. Returns True if valid."""
    print(f"\n{'='*60}")
    print(f"FILE: {path}")
    print('='*60)

    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    meta  = parse_header(text)
    gtin  = meta.get('gtin', 'unknown')
    model = meta.get('model', 'unknown')

    print(f"GTIN  : {gtin}")
    print(f"MODEL : {model}")

    parsed, warnings = parse_response(text)

    if warnings:
        print(f"\nPARSE WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ! {w}")

    coerced, errors = coerce(parsed)

    if errors:
        print(f"\nCOERCE ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  x {e}")
        if strict:
            return False

    schema = to_schema(coerced, gtin, model)

    print(f"\nSCHEMA OUTPUT:")
    print(json.dumps(schema, indent=2, ensure_ascii=False))

    valid = len(errors) == 0
    print(f"\nRESULT: {'PASS' if valid else 'FAIL'}")
    return valid


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Stage 2: Parse LLM response -> validate -> pretty print."
    )
    parser.add_argument('files', nargs='+',
        help="Response file(s) from llm_push.py")
    parser.add_argument('--strict', action='store_true',
        help="Exit non-zero if any errors found")
    args = parser.parse_args()

    results = []
    for path in args.files:
        ok = process_file(path, args.strict)
        results.append((path, ok))

    if len(results) > 1:
        print(f"\n{'='*60}")
        print(f"BATCH SUMMARY: {sum(1 for _, ok in results if ok)}/{len(results)} passed")
        for path, ok in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {os.path.basename(path)}")

    if args.strict and not all(ok for _, ok in results):
        sys.exit(1)
