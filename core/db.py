from __future__ import annotations
#
#  core/db.py
#
#  MariaDB connection pool and enrichment registry for Trigzi.
#
#  Usage:
#      from core.db import get_conn, get_or_create_enrichment
#
#      with get_conn() as conn:
#          with conn.cursor() as cur:
#              cur.execute("SELECT data FROM products WHERE gtin = %s", (gtin,))
#              row = cur.fetchone()
#
#      enrichment_id = get_or_create_enrichment(
#          task        = "product",
#          llm_model   = "gemini-2.5-flash",
#          prompt_ver  = "extract_v2",
#          prompt_text = prompt_string,
#      )
#

import os
import hashlib
import pymysql
import pymysql.cursors
from dbutils.pooled_db import PooledDB
from typing import Optional

# --- Connection pool config ---

_CONFIG = {
    "host":        os.environ.get("DB_HOST", "localhost"),
    "db":          os.environ.get("DB_NAME", "trigzi"),
    "user":        os.environ.get("DB_USER", "trigzi"),
    "passwd":      os.environ.get("DB_PASS", ""),
    "charset":     "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit":  True,
}

_pool: Optional[PooledDB] = None


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator        = pymysql,
            mincached      = 2,
            maxcached      = 8,
            maxconnections = 20,
            blocking       = True,
            **_CONFIG,
        )
    return _pool


def get_conn() -> pymysql.connections.Connection:
    """
    Return a pooled connection.
    Use as a context manager — connection is returned to pool on exit.
    """
    return _get_pool().connection()


def ping() -> bool:
    """Quick connectivity check. Returns True if DB is reachable."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception as e:
        print(f"  [!] DB ping failed: {e}")
        return False


# --- Enrichment registry ---

def get_or_create_enrichment(
    task:        str,
    llm_model:   str,
    prompt_ver:  str,
    prompt_text: str,
) -> Optional[int]:
    """
    Look up or insert an enrichment row identified by (prompt_hash, llm_model).

    The composite unique key ensures that the same prompt through a different
    model gets its own row — prompt×model is the meaningful unit of enrichment
    quality, not prompt alone.

    Returns the enrichment id, or None on error.

    Re-enrichment query pattern:
        SELECT gtin FROM products
        WHERE enrichment_id IN (
            SELECT id FROM enrichments
            WHERE prompt_ver != 'extract_v3'
            OR llm_model NOT IN ('gemini-2.5-flash')
        )
    """
    h = hashlib.sha256(prompt_text.encode()).hexdigest()[:8]
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT IGNORE INTO enrichments
                        (task, llm_model, prompt_ver, prompt_hash, prompt_text)
                    VALUES (%s, %s, %s, %s, %s)
                """, (task, llm_model, prompt_ver, h, prompt_text))

                cur.execute(
                    "SELECT id FROM enrichments WHERE prompt_hash = %s AND llm_model = %s",
                    (h, llm_model)
                )
                row = cur.fetchone()
                return row["id"] if row else None
    except Exception as e:
        print(f"  [!] get_or_create_enrichment error: {e}")
        return None
