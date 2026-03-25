#!/usr/bin/env python3 

from __future__ import annotations

"""
import_off_to_db.py

Walks the existing /var/www/off shard tree, promotes each record to the
unified product schema, and upserts into the MariaDB products table.

This replaces reshard_off.py — same promote() logic, same walk,
but writes to MariaDB instead of a new file tree.

Schema corrections applied (same as reshard_off.py):
  - Skip records with placeholder names (Unknown Product, empty)
  - raw_ingredients  <- raw_ingredients or ingredients_raw
  - fibre_g          <- fiber_g (British spelling)
  - sodium_mg        <- sodium_g * 1000
  - energy_kj        <- calories_kcal * 4.18
  - source           <- "off"
  - _source_id       <- gtin
  - _source_name     <- "off"
  - _enrichment_llm  <- null (unless already set)
  - parsed_ingredients <- []  (if missing)
  - clinical_profile <- null  (if missing)
  - package_size     <- ""    (if missing)
  - subcategory      <- ""    (if missing)

Safe to re-run — uses INSERT ... ON DUPLICATE KEY UPDATE.

Usage:
    python3 import_off_to_db.py                        # dry run
    python3 import_off_to_db.py --write                # import all
    python3 import_off_to_db.py --write --limit 10000  # test
"""

import os
import sys
import json
import argparse
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.db import get_conn

DEFAULT_SOURCE = "/var/www/off"
BATCH_SIZE     = 500

# Standard retail GTIN lengths only — filter OFF placeholder codes
VALID_GTIN_LENGTHS = {8, 12, 13, 14}

PLACEHOLDER_NAMES = {
    "unknown product",
    "unknown",
    "",
}

UPSERT_SQL = """
    INSERT INTO products (gtin, source, name, enriched, data)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        source     = VALUES(source),
        name       = VALUES(name),
        enriched   = VALUES(enriched),
        data       = VALUES(data),
        updated_at = CURRENT_TIMESTAMP
"""

# InnoDB bulk import settings
BULK_START_SQL = [
    "SET unique_checks = 0",
    "SET foreign_key_checks = 0",
    "SET autocommit = 0",
]

BULK_END_SQL = [
    "COMMIT",
    "SET unique_checks = 1",
    "SET foreign_key_checks = 1",
    "SET autocommit = 1",
]


def promote(record: dict) -> Optional[dict]:
    gtin = record.get("gtin", "").strip()
    if not gtin or not gtin.isdigit():
        return None
    if len(gtin) not in VALID_GTIN_LENGTHS:
        return None

    name = (record.get("name") or record.get("title") or "").strip()
    if name.lower() in PLACEHOLDER_NAMES:
        return None

    source = record.get("source") or record.get("_source_name") or "off"
    if source == "openfoodfacts":
        source = "off"

    raw_ingredients = (
        record.get("raw_ingredients") or
        record.get("ingredients_raw") or
        ""
    ).strip()

    n = record.get("nutrition_100g") or {}

    kcal      = n.get("calories_kcal") or n.get("energy_kcal")
    energy_kj = n.get("energy_kj")
    if energy_kj is None and kcal is not None:
        energy_kj = round(kcal * 4.18, 2)

    sodium_mg = n.get("sodium_mg")
    if sodium_mg is None:
        sodium_g = n.get("sodium_g")
        if sodium_g is not None:
            sodium_mg = round(sodium_g * 1000, 4)

    fibre_g = n.get("fibre_g") or n.get("fiber_g")

    return {
        "gtin":               gtin,
        "source":             source,
        "brand":              (record.get("brand") or "").strip(),
        "name":               name,
        "image_url":          (record.get("image_url") or ""),
        "package_size":       (record.get("package_size") or ""),
        "category":           (record.get("category") or "").strip(),
        "subcategory":        (record.get("subcategory") or ""),
        "health_star_rating": record.get("health_star_rating"),
        "serving_size_g":     record.get("serving_size_g"),
        "servings_per_pack":  record.get("servings_per_pack"),
        "nutrition_100g": {
            "energy_kj":       energy_kj,
            "calories_kcal":   kcal,
            "protein_g":       n.get("protein_g"),
            "fat_total_g":     n.get("fat_total_g"),
            "fat_saturated_g": n.get("fat_saturated_g"),
            "carbohydrates_g": n.get("carbohydrates_g"),
            "sugars_g":        n.get("sugars_g"),
            "fibre_g":         fibre_g,
            "sodium_mg":       sodium_mg,
        },
        "raw_ingredients":    raw_ingredients,
        "parsed_ingredients": record.get("parsed_ingredients") or [],
        "clinical_profile":   record.get("clinical_profile"),
        "_source_id":         gtin,
        "_source_name":       source,
        "_enrichment_llm":    record.get("_enrichment_llm"),
    }


def run(source_dir: str, write: bool, limit: int) -> None:
    print(f"{'DRY RUN — ' if not write else ''}Importing OFF tree to MariaDB")
    print(f"  Source : {source_dir}")
    print(f"  Limit  : {limit or 'unlimited'}\n")

    processed = 0
    imported  = 0
    skipped   = 0
    errors    = 0
    batch: list[tuple] = []

    conn = get_conn() if write else None

    def flush() -> None:
        nonlocal imported
        if not batch or not conn:
            return
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, batch)
        conn.commit()
        imported += len(batch)
        batch.clear()

    try:
        if write and conn:
            with conn.cursor() as cur:
                for sql in BULK_START_SQL:
                    cur.execute(sql)
            print("  Bulk import mode enabled (unique_checks=0, autocommit=0)")

        for root, dirs, files in os.walk(source_dir):
            dirs.sort()
            for fname in sorted(files):
                if not fname.endswith(".json"):
                    continue
                if limit and processed >= limit:
                    break

                path = os.path.join(root, fname)
                processed += 1

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        record = json.load(f)

                    promoted = promote(record)
                    if not promoted:
                        skipped += 1
                        continue

                    if write:
                        enriched = 1 if promoted.get("_enrichment_llm") else 0
                        batch.append((
                            promoted["gtin"],
                            promoted["source"],
                            promoted["name"][:150],
                            enriched,
                            json.dumps(promoted, ensure_ascii=False),
                        ))
                        if len(batch) >= BATCH_SIZE:
                            flush()
                    else:
                        imported += 1

                except (json.JSONDecodeError, OSError) as e:
                    errors += 1
                    if errors <= 10:
                        print(f"\n  ⚠️  {path}: {e}")

                if processed % 50_000 == 0:
                    sys.stdout.write(
                        f"\r  Processed: {processed:,} | "
                        f"Imported: {imported:,} | "
                        f"Skipped: {skipped:,} | "
                        f"Errors: {errors}"
                    )
                    sys.stdout.flush()

            else:
                continue
            break

        flush()

        if write and conn:
            with conn.cursor() as cur:
                for sql in BULK_END_SQL:
                    cur.execute(sql)
            print("\n  Bulk import mode disabled, indexes rebuilding...")

    finally:
        if conn:
            conn.close()

    print(f"\n\n✅ Done.")
    print(f"   Processed : {processed:,}")
    print(f"   Imported  : {imported:,}")
    print(f"   Skipped   : {skipped:,}  (placeholder name or no GTIN)")
    print(f"   Errors    : {errors}")

    if not write:
        print(f"\n   ℹ️  Dry run — nothing written. Pass --write to import.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import OFF shard tree into MariaDB products table."
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE,
        help=f"Source shard tree (default: {DEFAULT_SOURCE})"
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Write to database (default: dry run)"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Stop after N records (0 = unlimited)"
    )
    args = parser.parse_args()
    run(args.source, args.write, args.limit)
