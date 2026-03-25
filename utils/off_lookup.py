#!/usr/bin/env python3

from __future__ import annotations

"""
utils/off_lookup.py

GTIN lookup against the MariaDB products table.

Keeps the same interface as the old file-based OFFLookup so callers
need no changes:

    from utils.off_lookup import OFFLookup
    lookup = OFFLookup()
    record = lookup.get("9352042000342")   # dict or None

CLI:
    ./utils/off_lookup.py 9352042000342
"""

import json
import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.db import get_conn

VALID_GTIN_LENGTHS = {8, 12, 13, 14}


class OFFLookup:

    def get(self, gtin: str) -> Optional[dict]:
        """Return the product record for a GTIN, or None if not found."""
        gtin = self._clean(gtin)
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
            print(f"  [!] off_lookup error: {e}")
            return None

    def exists(self, gtin: str) -> bool:
        """Return True if a record exists for this GTIN."""
        gtin = self._clean(gtin)
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
        """Return True if the record has been LLM-enriched."""
        gtin = self._clean(gtin)
        if not gtin:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT enriched FROM products WHERE gtin = %s",
                        (gtin,)
                    )
                    row = cur.fetchone()
            return bool(row and row["enriched"])
        except Exception:
            return False

    def save(self, record: dict) -> bool:
        """
        Upsert an enriched record back to the database.
        Sets enriched=1 if _enrichment_llm is present.
        """
        gtin = self._clean(record.get("gtin", ""))
        if not gtin:
            return False
        try:
            enriched = 1 if record.get("_enrichment_llm") else 0
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO products (gtin, source, name, enriched, data)
                        VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            source     = VALUES(source),
                            name       = VALUES(name),
                            enriched   = VALUES(enriched),
                            data       = VALUES(data),
                            updated_at = CURRENT_TIMESTAMP
                    """, (
                        gtin,
                        record.get("source", "off"),
                        record.get("name", "")[:150],
                        enriched,
                        json.dumps(record, ensure_ascii=False),
                    ))
            return True
        except Exception as e:
            print(f"  [!] off_lookup.save error: {e}")
            return False

    def _clean(self, gtin: str) -> Optional[str]:
        if not gtin or not isinstance(gtin, str):
            return None
        gtin = gtin.strip()
        if not gtin.isdigit():
            return None
        if len(gtin) not in VALID_GTIN_LENGTHS:
            return None
        return gtin


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
