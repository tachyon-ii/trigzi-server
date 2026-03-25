from __future__ import annotations
"""
core/db.py

MariaDB connection pool for Trigzi.
Credentials from environment variables set in /etc/systemd/system/trigzi_api.service.

Usage:
    from core.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM products WHERE gtin = %s", (gtin,))
            row = cur.fetchone()
"""

import os
import pymysql
import pymysql.cursors
from dbutils.pooled_db import PooledDB

# --- Config from environment ---

_CONFIG = {
    "host":    os.environ.get("DB_HOST",   "localhost"),
    "db":      os.environ.get("DB_NAME",   "trigzi"),
    "user":    os.environ.get("DB_USER",   "trigzi"),
    "passwd":  os.environ.get("DB_PASS",   ""),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": True,
}

# --- Connection pool ---
# mincached  — connections kept open when idle
# maxcached  — max idle connections in pool
# maxconnections — hard cap (0 = unlimited)

_pool: PooledDB | None = None

def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator       = pymysql,
            mincached     = 2,
            maxcached     = 8,
            maxconnections= 20,
            blocking      = True,
            **_CONFIG,
        )
    return _pool


def get_conn() -> pymysql.connections.Connection:
    """
    Return a pooled connection.
    Use as a context manager — connection is returned to pool on exit.

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
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
