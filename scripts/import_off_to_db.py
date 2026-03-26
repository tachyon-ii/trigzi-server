#!/usr/bin/env python3
from __future__ import annotations
"""
scripts/import_off_to_db.py

Imports the raw Open Food Facts JSONL dump into the MariaDB products table.

Dry run mode dumps the JSON struct and INSERT SQL to stdout without
touching the database.

Usage:
    # Dry run — first 5 records
    ./scripts/import_off_to_db.py --input /data2000/openfoodfacts-products.jsonl --dry-run --limit 5

    # Full import
    ./scripts/import_off_to_db.py --input /data2000/openfoodfacts-products.jsonl --write

    # Test 1000 records
    ./scripts/import_off_to_db.py --input /data2000/openfoodfacts-products.jsonl --write --limit 1000
"""

import os
import sys
import json
import argparse
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.gtin import normalise

BATCH_SIZE = 500

PLACEHOLDER_NAMES = {
    'unknown product', 'unknown', 'null', 'n/a', '',
}

# Records with these data quality errors have unreliable nutrition data
# — null out the entire nutrition block rather than import garbage
NUTRITION_ERROR_TAGS = {
    'en:nutrition-value-total-over-105',
    'en:energy-value-in-kcal-does-not-match-value-computed-from-other-nutrients',
}

UPSERT_SQL = """
    INSERT INTO products (gtin, source, name, enrichment_id, data)
    VALUES (%s, %s, %s, NULL, %s)
    ON DUPLICATE KEY UPDATE
        source     = IF(source = 'off', VALUES(source), source),
        name       = IF(source = 'off', VALUES(name),   name),
        data       = IF(source = 'off', VALUES(data),   data),
        updated_at = CURRENT_TIMESTAMP
"""

BULK_START = [
    "SET unique_checks = 0",
    "SET foreign_key_checks = 0",
    "SET autocommit = 0",
    "ALTER TABLE products DISABLE KEYS",
    "DROP INDEX idx_name    ON products",
    "DROP INDEX idx_source  ON products",
    "DROP INDEX idx_updated ON products",
]
BULK_END = [
    "COMMIT",
    "ALTER TABLE products ENABLE KEYS",
    "CREATE INDEX idx_name    ON products (name)",
    "CREATE INDEX idx_source  ON products (source)",
    "CREATE INDEX idx_updated ON products (updated_at)",
    "SET unique_checks = 1",
    "SET foreign_key_checks = 1",
    "SET autocommit = 1",
]

KJ_PER_KCAL = 4.184

# Schema template — deep-copied per record, never mutated directly
SCHEMA_TEMPLATE = {
    "gtin":             "",
    "source":           "off",
    "brand":            "",
    "name":             "",
    "image_url":        "",
    "package_size":     "",
    "category":         "",
    "subcategory":      "",
    "health_star_rating": None,
    "serving_size_g":   None,
    "servings_per_pack": None,
    "nutrition_100g": {
        "energy_kj":        None,
        "calories_kcal":    None,
        "protein_g":        None,
        "fat_total_g":      None,
        "fat_saturated_g":  None,
        "carbohydrates_g":  None,
        "sugars_g":         None,
        "fibre_g":          None,
        "sodium_mg":        None,
    },
    "raw_ingredients":    "",
    "parsed_ingredients": [],
    "clinical_profile":   None,
    "_source_id":         "",
    "_source_name":       "off",
    "_enrichment_llm":    None,
}


def build_image_url(gtin: str, images: dict) -> str:
    if not images or not isinstance(images, dict):
        return ""
    front = images.get('selected', {}).get('front', {})
    if not front:
        return ""
    lang = 'en' if 'en' in front else next(iter(front.keys()), None)
    if not lang:
        return ""
    entry = front[lang]
    rev = entry.get('rev') if isinstance(entry, dict) else None
    if not rev:
        return ""
    g = gtin
    path = f"{g[0:3]}/{g[3:6]}/{g[6:9]}/{g[9:]}"
    return f"https://images.openfoodfacts.org/images/products/{path}/front_{lang}.{rev}.400.jpg"


def extract_allergens(tags: list) -> list:
    if not tags:
        return []
    result = []
    for tag in tags:
        name = tag.split(':', 1)[1] if ':' in tag else tag
        name = name.replace('-', ' ').strip().title()
        if name:
            result.append(name)
    return result


CATEGORY_BLACKLIST = {'null', 'unknown', ''}

def extract_category(tags: list) -> str:
    if not tags:
        return ""
    en_tags = [t for t in tags if t.startswith('en:')]
    if not en_tags:
        return ""
    for tag in reversed(en_tags):
        name = tag.split(':', 1)[1].replace('-', ' ').strip().lower()
        if name not in CATEGORY_BLACKLIST:
            return name.title()
    return ""


def to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def to_str(val, maxlen: int = 0) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    return s[:maxlen] if maxlen else s


def extract(raw: dict) -> Optional[dict]:
    gtin = normalise(raw.get('code', ''))
    if not gtin:
        return None

    name = (
        to_str(raw.get('product_name_en')) or
        to_str(raw.get('product_name'))
    )
    if name.lower() in PLACEHOLDER_NAMES:
        return None

    n = raw.get('nutriments', {}) or {}

    # energy_100g is in kJ despite ambiguous name
    energy_kj     = to_float(n.get('energy_100g'))
    calories_kcal = to_float(n.get('energy-kcal_100g'))

    if energy_kj is None and calories_kcal is not None:
        energy_kj = round(calories_kcal * KJ_PER_KCAL, 2)
    elif calories_kcal is None and energy_kj is not None:
        calories_kcal = round(energy_kj / KJ_PER_KCAL, 2)

    # Drop records with any data quality errors
    if raw.get('data_quality_errors_tags'):
        return None

    quality_errors = set(raw.get('data_quality_errors_tags', []))
    bad_nutrition   = bool(quality_errors & NUTRITION_ERROR_TAGS)

    sodium_g  = to_float(n.get('sodium_100g'))
    sodium_mg = round(sodium_g * 1000, 4) if sodium_g is not None else None

    # Deep copy template — no shared state between records
    record = json.loads(json.dumps(SCHEMA_TEMPLATE))

    record["gtin"]              = gtin
    record["brand"]             = to_str(raw.get('brands'), 100)
    record["name"]              = name[:150]
    record["image_url"]         = build_image_url(gtin, raw.get('images', {}))
    record["package_size"]      = to_str(raw.get('quantity'), 50)
    record["category"]          = extract_category(raw.get('categories_tags', []))
    record["serving_size_g"]    = to_float(raw.get('serving_quantity'))
    record["_source_id"]        = gtin

    record["nutrition_100g"] = None if bad_nutrition else {
        "energy_kj":        energy_kj,
        "calories_kcal":    calories_kcal,
        "protein_g":        to_float(n.get('proteins_100g')),
        "fat_total_g":      to_float(n.get('fat_100g')),
        "fat_saturated_g":  to_float(n.get('saturated-fat_100g')),
        "carbohydrates_g":  to_float(n.get('carbohydrates_100g')),
        "sugars_g":         to_float(n.get('sugars_100g')),
        "fibre_g":          to_float(n.get('fiber_100g')),
        "sodium_mg":        sodium_mg,
    }

    raw_ingredients = (
        to_str(raw.get('ingredients_text_en')) or
        to_str(raw.get('ingredients_text'))
    )
    record["raw_ingredients"] = raw_ingredients[:1000]

    allergens = extract_allergens(raw.get('allergens_tags', []))
    if allergens:
        record["clinical_profile"] = {
            "estimated_health_star": None,
            "fodmap_rating":         -1,
            "coeliac_rating":        -1,
            "histamine_rating":      -1,
            "allergen_warnings":     allergens,
            "health_summary":        "",
        }

    return record


def run(input_file: str, write: bool, limit: int, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "WRITE"
    print(f"OFF Importer — {mode}")
    print(f"  Input : {input_file}")
    print(f"  Limit : {limit or 'unlimited'}\n")

    processed = 0
    imported  = 0
    skipped   = 0
    errors    = 0
    batch: list[tuple] = []

    conn = None
    if write and not dry_run:
        from core.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            for sql in BULK_START:
                cur.execute(sql)
        print("  Bulk import mode enabled\n")

    def flush() -> None:
        nonlocal imported
        if not batch or not conn:
            return
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, batch)
        conn.commit()
        imported += len(batch)
        batch.clear()

    def progress() -> None:
        elapsed = time.time() - t0
        rate    = processed / elapsed if elapsed > 0 else 0
        sys.stdout.write(
            f"\r  Processed: {processed:,} | "
            f"Imported: {imported:,} | "
            f"Skipped: {skipped:,} | "
            f"Errors: {errors} | "
            f"{rate:.0f} rec/s   "
        )
        sys.stdout.flush()

    t0 = time.time()

    try:
        with open(input_file, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if limit and processed >= limit:
                    break

                processed += 1

                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue

                record = extract(raw)
                if not record:
                    skipped += 1
                    continue

                data_json = json.dumps(record, ensure_ascii=False)
                row = (record['gtin'], record['source'], record['name'], data_json)

                if dry_run:
                    print(f"{'─'*60}")
                    print(f"GTIN   : {record['gtin']}")
                    print(f"INSERT : ({row[0]!r}, {row[1]!r}, {row[2]!r}, NULL, <data>)")
                    print(f"JSON   :")
                    print(json.dumps(record, indent=2, ensure_ascii=False))
                    print()
                    imported += 1
                else:
                    batch.append(row)
                    if len(batch) >= BATCH_SIZE:
                        flush()

                if not dry_run and processed % 5_000 == 0:
                    progress()

        if not dry_run:
            flush()
            if conn:
                with conn.cursor() as cur:
                    for sql in BULK_END:
                        cur.execute(sql)
                print("\n  Bulk import mode disabled")

    finally:
        if conn:
            conn.close()

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Processed : {processed:,}")
    print(f"  Imported  : {imported:,}")
    print(f"  Skipped   : {skipped:,}  (invalid GTIN or placeholder name)")
    print(f"  Errors    : {errors}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Import OFF JSONL into MariaDB products table."
    )
    parser.add_argument('--input', required=True,
                        help="Path to unzipped OFF JSONL dump")
    parser.add_argument('--write', action='store_true',
                        help="Write to database")
    parser.add_argument('--dry-run', action='store_true',
                        help="Print JSON and INSERT statements, no DB writes")
    parser.add_argument('--limit', type=int, default=0,
                        help="Stop after N records (0 = unlimited)")
    args = parser.parse_args()

    if not args.write and not args.dry_run:
        args.dry_run = True

    run(args.input, args.write, args.limit, args.dry_run)
