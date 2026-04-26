#!/usr/bin/env python3
"""
=============================================================================
Module:        Enriched Products Importer
Location:      scripts/import_enriched.py
Description:   Imports validated enriched_products.jsonl into the MariaDB
               products table. Enriched records overwrite OFF records for
               the same GTIN. All GTINs pass through utils/gtin.normalise()
               — nothing enters the DB without a valid normalised GTIN.

Architecture Note:
This script runs the same async core.db pool that the Quart server uses,
but stands up its own lifecycle: it awaits init_pool() before any DB work
and close_pool() at exit. A single connection is held for the entire
import duration so the SET autocommit = 0 / index-drop bulk-mode settings
persist across all batches — releasing per-batch would lose the session
state and the bulk-mode wouldn't actually apply.

Usage:
    ./scripts/import_enriched.py --input /data2000/enriched_products_normalised.jsonl
    ./scripts/import_enriched.py --input /data2000/enriched_products_normalised.jsonl --write
=============================================================================
"""

# pylint: disable=duplicate-code
# The JSONL-strip-and-parse loop body is shared with
# scripts/normalise_enriched_gtins.py. Both scripts are independent
# CLI tools and the 8-line loop is shorter than any reasonable shared
# helper would be — extracting it would add a callback-style helper
# that's harder to read than the inline form.

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

try:
    from core.db import close_pool, get_pool, init_pool
    from utils.gtin import normalise
except ImportError:
    # Allow running from project root without installing the package
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    # pylint: disable=ungrouped-imports
    # The duplicated imports across try/except are intentional — pylint
    # sees them as ungrouped because the sys.path bootstrap sits between,
    # but the canonical-first / fallback-second pattern requires both.
    from core.db import close_pool, get_pool, init_pool
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


async def run(input_file: str, write: bool) -> None:
    """Stream the enriched JSONL into the products table.

    Outer wrapper: handles the pool-acquire lifecycle so the inner worker
    sees a single live connection (or None for dry-run mode). Splitting
    this from _run_with_connection keeps the connection scope explicit
    via ``async with`` rather than manually invoking the dunder protocol.
    """
    if write:
        pool = get_pool()
        async with pool.acquire() as conn:
            await _run_with_connection(input_file, write, conn)
    else:
        await _run_with_connection(input_file, write, None)


async def _run_with_connection(input_file: str, write: bool, conn) -> None:  # pylint: disable=too-many-branches,too-many-statements
    """Streaming import body — runs against a held connection (or None for dry-run).

    The body is intentionally a single linear pipeline (open → parse →
    validate → normalise → batch → flush → progress → cleanup) rather
    than decomposed into helpers, since the per-record work is cheap
    and threading the batch + connection through helper functions would
    obscure the streaming flow without saving meaningful complexity.
    """
    print(f"{'DRY RUN — ' if not write else ''}Importing enriched JSONL to MariaDB")
    print(f"  Input  : {input_file}\n")

    processed = 0
    imported  = 0
    skipped   = 0
    errors    = 0
    batch: list[tuple] = []

    async def flush() -> None:
        """Drain accumulated batch via executemany + commit, then clear it."""
        nonlocal imported
        if not batch or not conn:
            return
        async with conn.cursor() as cur:
            await cur.executemany(UPSERT_SQL, batch)
        await conn.commit()
        imported += len(batch)
        batch.clear()

    def progress() -> None:
        """Render a one-line counter to stderr in-place."""
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

    if write and conn:
        async with conn.cursor() as cur:
            for sql in BULK_START:
                await cur.execute(sql)
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

            # Normalise Woolworths image URL: large → medium
            img = record.get('image_url', '')
            if img and 'wowproductimages/large/' in img:
                record['image_url'] = img.replace(
                    'wowproductimages/large/',
                    'wowproductimages/medium/'
                )

            if write:
                batch.append((
                    gtin,
                    record.get('source', ''),
                    record.get('name', '')[:150],
                    json.dumps(record, ensure_ascii=False),
                ))
                if len(batch) >= BATCH_SIZE:
                    await flush()
            else:
                imported += 1

            if processed % 5_000 == 0:
                progress()

    await flush()

    if write and conn:
        async with conn.cursor() as cur:
            for sql in BULK_END:
                await cur.execute(sql)
        print("\n  Bulk import mode disabled")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Processed : {processed:,}")
    print(f"  Imported  : {imported:,}")
    print(f"  Skipped   : {skipped:,}  (invalid GTIN)")
    print(f"  Errors    : {errors}")

    if not write:
        print("\n  Dry run — nothing written. Pass --write to import.")


async def main(input_file: str, write: bool) -> None:
    """Stand up the DB pool, run the import, and tear the pool down cleanly."""
    if write:
        await init_pool()
    try:
        await run(input_file, write)
    finally:
        if write:
            await close_pool()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Import enriched JSONL into MariaDB products table."
    )
    parser.add_argument('--input',  required=True, help="Path to enriched JSONL")
    parser.add_argument('--write',  action='store_true', help="Write to database")
    args = parser.parse_args()
    asyncio.run(main(args.input, args.write))
