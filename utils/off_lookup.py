#!/usr/bin/env python3
"""
=============================================================================
Module:        OFF Product Lookup
Location:      utils/off_lookup.py
Description:   GTIN-keyed read/write API for the local Open Food Facts
                products mirror in MariaDB. Provides a lightweight
                async interface over the products table — get the
                full record, check existence, check enrichment status,
                or upsert a record with optional enrichment linkage.

Architecture Note:
A module-level singleton (``lookup``) is exposed for callers; the
class is instantiable but stateless — every call is a fresh DB round
trip via core.db's pool. GTIN normalisation runs at every entry point
so callers can pass anything the scanners produce (EAN-8, UPC, EAN-13,
EAN-14) without per-callsite cleanup.

The CLI mode (__main__) needs explicit pool init/close because it
runs outside the Hypercorn lifespan that normally manages the pool.
For application code, the pool is already alive when these methods
are called.

Usage (programmatic):
    from utils.off_lookup import lookup
    record = await lookup.get("9352042000342")   # dict or None
    await lookup.save(record, enrichment_id=42)

Usage (CLI):
    python utils/off_lookup.py 9352042000342
=============================================================================
"""

# pylint: disable=duplicate-code
# Justification: shares the standard async pool.acquire / conn.cursor
# / cur.execute pattern with core/db.py. See core/db.py for the
# rationale on why this isn't extracted.

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Optional

# sys.path bootstrap so this script can be invoked as `python utils/off_lookup.py`
# from anywhere. The wrapping try/except around the imports keeps both pylint
# (C0413 wrong-import-position) and the runtime happy: the path mutation must
# precede the project imports, and the try block declares that intent.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from core.db import close_pool, get_pool, init_pool  # pylint: disable=ungrouped-imports
    from utils.gtin import normalise                       # pylint: disable=ungrouped-imports
except ImportError as exc:
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)


class OFFLookup:
    """Async CRUD wrapper around the products table.

    Stateless — every call acquires a fresh pool connection. The
    methods are split into a Read group (get / exists / is_enriched)
    and a Write group (save / upsert). Errors are caught and logged
    rather than raised so callers can treat lookup failure as a
    cache miss without try/except boilerplate at every site.
    """

    # MARK: - Read

    async def get(self, gtin: str) -> Optional[dict]:
        """Return the product record for a GTIN, or None if not found."""
        gtin = normalise(gtin)
        if not gtin:
            return None
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT data FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    row = await cur.fetchone()

            if not row:
                return None
            data = row["data"]
            return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            print(f"  [!] off_lookup.get error: {e}")
            return None

    async def exists(self, gtin: str) -> bool:
        """Return True if a record exists for this GTIN."""
        gtin = normalise(gtin)
        if not gtin:
            return False
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT 1 FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    return await cur.fetchone() is not None
        except Exception:
            return False

    async def is_enriched(self, gtin: str) -> bool:
        """Return True if the record has an enrichment_id set."""
        gtin = normalise(gtin)
        if not gtin:
            return False
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT enrichment_id FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    row = await cur.fetchone()
            return bool(row and row["enrichment_id"])
        except Exception:
            return False

    # MARK: - Write

    async def save(self, record: dict, enrichment_id: Optional[int] = None) -> bool:
        """Upsert a product record.

        Returns True on successful write, False if normalisation fails
        or the DB write errors. The ON DUPLICATE KEY clause preserves
        an existing enrichment_id when the new record doesn't carry
        one — partial updates from non-enriching writers won't clear
        the previously-recorded enrichment link.
        """
        gtin = normalise(record.get("gtin", ""))
        if not gtin:
            return False
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO products (gtin, source, name, enrichment_id, data)
                        VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            source        = VALUES(source),
                            name          = VALUES(name),
                            enrichment_id = IF(VALUES(enrichment_id) IS NOT NULL,
                                              VALUES(enrichment_id),
                                              enrichment_id),
                            data          = VALUES(data),
                            updated_at    = CURRENT_TIMESTAMP
                    """, (
                        gtin,
                        record.get("source", "off"),
                        record.get("name",   "")[:150],
                        enrichment_id,
                        json.dumps(record, ensure_ascii=False),
                    ))
            return True
        except Exception as e:
            print(f"  [!] off_lookup.save error: {e}")
            return False


# Module-level singleton — callers should use this rather than instantiating.
lookup = OFFLookup()


async def _cli_main(gtin_arg: str) -> None:
    """CLI entry point: open the pool, look up one GTIN, print, close pool."""
    await init_pool()
    try:
        record = await lookup.get(gtin_arg)
        if record:
            print(json.dumps(record, indent=2, ensure_ascii=False))
        else:
            print(f"Not found: {gtin_arg}")
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Look up a GTIN in the products DB.")
    parser.add_argument("gtin", help="GTIN to look up")
    args = parser.parse_args()

    asyncio.run(_cli_main(args.gtin))
