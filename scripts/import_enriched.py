#!/usr/bin/env python3
from __future__ import annotations
"""
scripts/import_enriched.py

Imports validated enriched_products.jsonl into the MariaDB products table.
Enriched records overwrite OFF records for the same GTIN.

All GTINs pass through utils/gtin.normalise() — nothing enters the DB
without a valid normalised GTIN.

Usage:
    ./scripts/import_enriched.py --input /data2000/enriched_products_normalised.jsonl
    ./scripts/import_enriched.py --input /data2000/enriched_products_normalised.jsonl --write
"""

import os
import sys
import json
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.db import get_conn
from utils.gtin import normalise

BATCH_SIZE = 500

UPSERT_SQL = """
    INSERT INTO products (gtin, source, name, enrichment_id, data)
    VALUES (%s, %s, %s, NULL, %s)
    ON DUPLICATE KEY UPDATE
        source        = VALUES(source),
        name          = VALUES(name),
        enrichment_id = VALUES(enrichment_id),
        data          = VALUES(data),
        updated_at    = CURRENT_TIMESTAMP
"""

BULK_START = [
    "SET unique_checks = 0",
    "SET foreign_key_checks = 0",
    "SET autocommit = 0",
    "DROP INDEX idx_name    ON products",
    "DROP INDEX idx_source  ON products",
    "DROP INDEX idx_updated ON products",
]

BULK_END = [
    "COMMIT",
    "CREATE INDEX idx_name    ON products (name)",
    "CREATE INDEX idx_source  ON products (source)",
    "CREATE INDEX idx_updated ON products (updated_at)",
    "SET unique_checks = 1",
    "SET foreign_key_checks = 1",
    "SET autocommit = 1",
]


def run(input_file: str, write: bool) -> None:
    print(f"{'DRY RUN — ' if not write else ''}Importing enriched JSONL to MariaDB")
    print(f"  Input  : {input_file}\n")

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
        if write and conn:
            with conn.cursor() as cur:
                for sql in BULK_START:
                    cur.execute(sql)
            print("  Bulk import mode enabled\n")

        with open(input_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                processed += 1

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    errors += 1
                    if errors <= 5:
                        print(f"  JSON error line {processed}: {e}")
                    continue

                # GTIN gate — normalise or drop
                gtin = normalise(str(record.get('gtin', '')))
                if not gtin:
                    skipped += 1
                    continue

                # Update record with normalised GTIN
                record['gtin']       = gtin
                record['_source_id'] = gtin

                if write:
                    batch.append((
                        gtin,
                        record.get('source', ''),
                        record.get('name', '')[:150],
                        json.dumps(record, ensure_ascii=False),
                    ))
                    if len(batch) >= BATCH_SIZE:
                        flush()
                else:
                    imported += 1

                if processed % 5_000 == 0:
                    progress()

        flush()

        if write and conn:
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
    print(f"  Skipped   : {skipped:,}  (invalid GTIN)")
    print(f"  Errors    : {errors}")

    if not write:
        print(f"\n  Dry run — nothing written. Pass --write to import.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Import enriched JSONL into MariaDB products table."
    )
    parser.add_argument('--input',  required=True, help="Path to enriched JSONL")
    parser.add_argument('--write',  action='store_true', help="Write to database")
    args = parser.parse_args()
    run(args.input, args.write)
