"""
=============================================================================
Module:        Database Pool
Location:      core/db.py
Description:   aiomysql connection pool and enrichment registry for Trigzi.
               Provides a process-wide singleton pool plus a helper for
               looking up or inserting rows in the `enrichments` table,
               which records every (prompt, model) pair the analyser
               sends to an LLM.

Architecture Note:
The pool is a module-level singleton initialised inside the Quart
event loop via init_pool() at app startup, and torn down via
close_pool() at shutdown. Callers obtain the pool through get_pool(),
which raises if init_pool() has not been awaited yet — this fail-fast
behaviour is preferable to silently returning None and surfacing the
problem deeper in the call stack.

Usage:
    from core.db import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT data FROM products WHERE gtin = %s", (gtin,))
            row = await cur.fetchone()
=============================================================================
"""

# pylint: disable=duplicate-code
# Justification: shares the standard async pool.acquire / conn.cursor
# / cur.execute pattern with utils/off_lookup.py. This is the canonical
# aiomysql usage; every async DB consumer in the project repeats it.
# Extracting a helper would either pass the SQL string and parameters
# (just moving the boilerplate) or hide the cursor lifecycle (worse for
# debugging). Accepting the duplicate.

from __future__ import annotations

import hashlib
import os
from typing import Optional

import aiomysql

# --- Connection pool config ---

_CONFIG = {
    "host":        os.environ.get("DB_HOST", "localhost"),
    "port":        int(os.environ.get("DB_PORT", 3306)),
    "user":        os.environ.get("DB_USER", "trigzi"),
    "password":    os.environ.get("DB_PASS", ""),
    "db":          os.environ.get("DB_NAME", "trigzi"),
    "autocommit":  True,
}

_pool: Optional[aiomysql.Pool] = None

async def init_pool():
    """Initializes the async connection pool. Must be called within the Quart event loop.

    The ``global _pool`` statement is required because this function is
    the singleton's lazy initialiser — it deliberately reassigns the
    module-level binding so subsequent ``from core.db import get_pool``
    calls see the live instance.
    """
    global _pool  # pylint: disable=global-statement
    if _pool is None:
        _pool = await aiomysql.create_pool(
            minsize=2,
            maxsize=8,
            cursorclass=aiomysql.DictCursor,
            **_CONFIG
        )
        print("✅ aiomysql connection pool initialized.")

async def close_pool():
    """Gracefully shuts down the connection pool."""
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        print("🛑 aiomysql connection pool closed.")

def get_pool() -> aiomysql.Pool:
    """Returns the active pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Did you forget to await init_pool()?")
    return _pool

async def ping() -> bool:
    """Quick connectivity check. Returns True if DB is reachable."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        return True
    except Exception as e:
        print(f"  [!] DB ping failed: {e}")
        return False

# --- Enrichment registry ---

async def get_or_create_enrichment(
    task:        str,
    llm_model:   str,
    prompt_ver:  str,
    prompt_text: str,
) -> Optional[int]:
    """
    Look up or insert an enrichment row identified by (prompt_hash, llm_model).
    """
    h = hashlib.sha256(prompt_text.encode()).hexdigest()[:8]

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT IGNORE INTO enrichments
                        (task, llm_model, prompt_ver, prompt_hash, prompt_text)
                    VALUES (%s, %s, %s, %s, %s)
                """, (task, llm_model, prompt_ver, h, prompt_text))

                await cur.execute(
                    "SELECT id FROM enrichments WHERE prompt_hash = %s AND llm_model = %s",
                    (h, llm_model)
                )
                row = await cur.fetchone()
                return row["id"] if row else None
    except Exception as e:
        print(f"  [!] get_or_create_enrichment error: {e}")
        return None
