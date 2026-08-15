"""
=============================================================================
Module:        SQLite OTA Store
Location:      core/sqlite_store.py
Description:   Read-only access to the three OTA-delivered SQLite files:
               gtin_cache.db, ingredient.db, and content.db.

               These are the same files delivered to devices via the OTA
               manifest. The server loads them at startup and queries them
               directly — no duplication into MariaDB.

               Used for:
                 - Phone-home GTIN lookups (gtin_cache.db)
                 - Canonical resolution for swap queries (gtin_cache.db)
                 - Content serving (content.db)
                 - Ingredient name → canonical_id for enrichment (ingredient.db)

Environment variables (all have defaults suitable for the server):
    GTIN_DB_PATH       default: /var/www/trigzi/data/gtin_cache.db
    INGREDIENT_DB_PATH default: /var/www/trigzi/data/ingredient.db
    CONTENT_DB_PATH    default: /var/www/trigzi/data/content.db

Thread safety:
    sqlite3 connections are not thread-safe across threads by default.
    We open connections with check_same_thread=False and serialise
    access with a threading.Lock per database. Quart offloads blocking
    calls via asyncio.to_thread(); the lock prevents concurrent reads
    from corrupting the connection state.
=============================================================================
"""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
from typing import Optional

# ── Path configuration ────────────────────────────────────────────────────────

_DATA_ROOT = os.environ.get("TRIGZI_DATA_ROOT", "/var/www/trigzi/data")

GTIN_DB_PATH       = os.environ.get("GTIN_DB_PATH",
                        os.path.join(_DATA_ROOT, "gtin_cache.db"))
INGREDIENT_DB_PATH = os.environ.get("INGREDIENT_DB_PATH",
                        os.path.join(_DATA_ROOT, "ingredient.db"))
CONTENT_DB_PATH    = os.environ.get("CONTENT_DB_PATH",
                        os.path.join(_DATA_ROOT, "content.db"))


# ── Connection singletons ─────────────────────────────────────────────────────

class _DB:
    """Lazy-opened, lock-protected SQLite connection."""

    def __init__(self, path: str, label: str) -> None:
        self._path  = path
        self._label = label
        self._conn: Optional[sqlite3.Connection] = None
        self._lock  = threading.Lock()

    def open(self) -> None:
        """Open and cache the connection. Called once at startup."""
        if not os.path.exists(self._path):
            print(f"  ⚠️  SQLiteStore: {self._label} not found at {self._path} — "
                  f"lookups will return None")
            return
        self._conn = sqlite3.connect(
            f"file:{self._path}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        print(f"  ✅ SQLiteStore: {self._label} loaded from {self._path}")

    @property
    def conn(self) -> Optional[sqlite3.Connection]:
        return self._conn

    @property
    def lock(self) -> threading.Lock:
        return self._lock


_gtin_db       = _DB(GTIN_DB_PATH,       "gtin_cache.db")
_ingredient_db = _DB(INGREDIENT_DB_PATH, "ingredient.db")
_content_db    = _DB(CONTENT_DB_PATH,    "content.db")


def open_all() -> None:
    """Open all three SQLite files. Call once from app startup (before_serving)."""
    _gtin_db.open()
    _ingredient_db.open()
    _content_db.open()


def close_all() -> None:
    """Close all connections. Call from app shutdown (after_serving)."""
    for db in (_gtin_db, _ingredient_db, _content_db):
        if db.conn:
            db.conn.close()


# ── Blob helpers ──────────────────────────────────────────────────────────────

def _decode_blob(blob: Optional[bytes]) -> list[int]:
    """Decode a gtin_cache.db BLOB (N × LE uint16_t) to a list of ints."""
    if not blob:
        return []
    count = len(blob) // 2
    return list(struct.unpack_from(f"<{count}H", blob))


# ── gtin_cache.db ─────────────────────────────────────────────────────────────

def get_product(gtin_int: int) -> Optional[dict]:
    """
    Look up a product by its integer EAN-13.

    Returns a dict with all gtin_cache.db columns plus decoded
    `canonicals_list` and `categories_list` convenience keys.
    Returns None if the GTIN is not in the DB or the DB is not loaded.
    """
    db = _gtin_db
    if not db.conn:
        return None
    with db.lock:
        row = db.conn.execute(
            "SELECT * FROM product WHERE gtin = ?", (gtin_int,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["canonicals_list"] = _decode_blob(d.get("canonicals"))
    d["categories_list"]  = _decode_blob(d.get("categories"))
    return d


def get_canonical_ids(gtin_int: int) -> list[int]:
    """Return the canonical ID list for a GTIN. Empty list if not found."""
    product = get_product(gtin_int)
    if not product:
        return []
    return product.get("canonicals_list", [])


def gtin_exists(gtin_int: int) -> bool:
    """True if the GTIN is in gtin_cache.db."""
    db = _gtin_db
    if not db.conn:
        return False
    with db.lock:
        row = db.conn.execute(
            "SELECT 1 FROM product WHERE gtin = ?", (gtin_int,)
        ).fetchone()
    return row is not None


def all_gtins_for_country(country_code: str = "AU") -> list[int]:
    """Return all GTINs for a country. Used by the image miner."""
    db = _gtin_db
    if not db.conn:
        return []
    with db.lock:
        rows = db.conn.execute(
            "SELECT gtin FROM product WHERE country = "
            "(SELECT id FROM country WHERE code = ?)",
            (country_code,)
        ).fetchall()
    return [r[0] for r in rows]


# ── ingredient.db ─────────────────────────────────────────────────────────────

def resolve_canonical_id(name: str) -> Optional[int]:
    """
    Resolve an ingredient name to its canonical_id via the alias table.
    Returns None if not found. Case-insensitive.
    """
    db = _ingredient_db
    if not db.conn:
        return None
    with db.lock:
        row = db.conn.execute(
            "SELECT canonical_id FROM alias WHERE name = ?", (name.lower(),)
        ).fetchone()
    return row["canonical_id"] if row else None


# ── content.db ────────────────────────────────────────────────────────────────

def get_content(canonical_id: int, lang: str = "en") -> Optional[dict]:
    """
    Fetch the rich description for a canonical by ID and language.

    Returns dict with keys: summary, what, origin, why, avoid_if.
    Returns None if not found.
    """
    db = _content_db
    if not db.conn:
        return None
    with db.lock:
        row = db.conn.execute(
            """
            SELECT cc.summary, cc.what, cc.origin, cc.why, cc.avoid_if,
                   cr.primary_name
            FROM   canonical_content cc
            JOIN   canonical_ref     cr ON cr.canonical_id = cc.canonical_id
            WHERE  cc.canonical_id = ? AND cc.lang = ?
            """,
            (canonical_id, lang),
        ).fetchone()
    return dict(row) if row else None


def get_content_by_name(name: str, lang: str = "en") -> Optional[dict]:
    """
    Fetch the rich description for a canonical by its primary name.
    Resolves via ingredient.db alias table first, then falls back to
    a direct canonical_ref lookup in content.db.
    """
    # Try ingredient.db alias resolution first
    cid = resolve_canonical_id(name)

    if cid is None:
        # Fallback: direct name lookup in canonical_ref
        db = _content_db
        if not db.conn:
            return None
        with db.lock:
            row = db.conn.execute(
                "SELECT canonical_id FROM canonical_ref WHERE primary_name = ?",
                (name,),
            ).fetchone()
        if row:
            cid = row["canonical_id"]

    if cid is None:
        return None

    return get_content(cid, lang)
