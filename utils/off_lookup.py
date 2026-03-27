#!/usr/bin/env python3
from __future__ import annotations
#
#  utils/off_lookup.py
#
#  GTIN lookup and persistence against the MariaDB products table.
#
#  Usage:
#      from utils.off_lookup import OFFLookup
#      lookup = OFFLookup()
#      record = lookup.get("9352042000342")   # dict or None
#      lookup.save(record, enrichment_id=42)
#
#  CLI:
#      ./utils/off_lookup.py 9352042000342
#

import json
import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.db import get_conn
from utils.gtin import normalise


class OFFLookup:

    # MARK: - Read

    def get(self, gtin: str) -> Optional[dict]:
        """Return the product record for a GTIN, or None if not found."""
        gtin = normalise(gtin)
        if not gtin:
            return None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    row = cur.fetchone()
            if not row:
                return None
            data = row["data"]
            return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            print(f"  [!] off_lookup.get error: {e}")
            return None

    def exists(self, gtin: str) -> bool:
        """Return True if a record exists for this GTIN."""
        gtin = normalise(gtin)
        if not gtin:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    return cur.fetchone() is not None
        except Exception:
            return False

    def is_enriched(self, gtin: str) -> bool:
        """Return True if the record has an enrichment_id set."""
        gtin = normalise(gtin)
        if not gtin:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT enrichment_id FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    row = cur.fetchone()
            return bool(row and row["enrichment_id"])
        except Exception:
            return False

    # MARK: - Write

    def save(self, record: dict, enrichment_id: Optional[int] = None) -> bool:
        """
        Upsert a product record.

        enrichment_id — FK to enrichments table. Pass None for unenriched records.
        When updating an existing record, enrichment_id is only written if
        the new value is not None (prevents overwriting a valid enrichment
        with None on a partial update).
        """
        gtin = normalise(record.get("gtin", ""))
        if not gtin:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
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


# Module-level singleton
lookup = OFFLookup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Look up a GTIN in the products DB.")
    parser.add_argument("gtin", help="GTIN to look up")
    args = parser.parse_args()

    record = lookup.get(args.gtin)
    if record:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print(f"Not found: {args.gtin}")
