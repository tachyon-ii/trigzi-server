"""
=============================================================================
Module:        Swap Query Runtime
Location:      core/swap.py
Description:   Loads the trigzi-db MinHash LSH swap index at startup and
               exposes a single async query method.

               Given a GTIN, returns ranked alternative GTINs whose
               canonical-ID sets are similar but do not overlap with a
               provided avoid set (the user's sensitivity canonicals).

Index files (built by trigzi-db/swap/build_index.py):
    SWAP_INDEX_PATH   default: /var/www/trigzi/data/index.pkl
    SWAP_META_PATH    default: /var/www/trigzi/data/meta.db

Load time: ~6.5 s (once at startup)
Query time: ~20 ms

Architecture note:
    The LSH query is CPU-bound and synchronous (datasketch). It is wrapped
    in asyncio.to_thread() so it does not block the Quart event loop.
    The loaded index is a module-level singleton — no per-request loading.

    minhashes are NOT stored in index.pkl. At query time the scanned product's
    MinHash is reconstructed from its canonicals blob in meta.db. This is correct
    because MinHash(num_perm, seed=1) uses a fixed permutation matrix — the
    reconstructed hash is bit-identical to the one inserted at build time.
=============================================================================
"""

from __future__ import annotations

import asyncio
import os
import pickle
import sqlite3
from typing import Optional

# ── Path configuration ────────────────────────────────────────────────────────

_DATA_ROOT = os.environ.get("TRIGZI_DATA_ROOT", "/var/www/trigzi/data")

SWAP_INDEX_PATH = os.environ.get(
    "SWAP_INDEX_PATH", os.path.join(_DATA_ROOT, "index.pkl")
)
SWAP_META_PATH = os.environ.get(
    "SWAP_META_PATH", os.path.join(_DATA_ROOT, "meta.db")
)

# ── Singletons ────────────────────────────────────────────────────────────────

_lsh:       Optional[object]              = None   # datasketch MinHashLSH
_num_perm:  int                           = 128    # loaded from index.pkl
_meta_conn: Optional[sqlite3.Connection]  = None


def load() -> None:
    """
    Load the swap index from disk. Call once at app startup (before_serving).
    Logs a warning and disables the swap endpoint gracefully if files are absent.
    """
    global _lsh, _num_perm, _meta_conn  # pylint: disable=global-statement

    if not os.path.exists(SWAP_INDEX_PATH):
        print(f"  ⚠️  swap: index not found at {SWAP_INDEX_PATH} — /swap endpoint disabled")
        return

    try:
        with open(SWAP_INDEX_PATH, "rb") as fh:
            payload = pickle.load(fh)

        # build_index.py saves {"lsh": lsh_object, "num_perm": int}
        _lsh      = payload["lsh"]
        _num_perm = payload.get("num_perm", 128)
        print(f"  ✅ swap: index loaded (num_perm={_num_perm})")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  ⚠️  swap: failed to load index: {exc}")
        return

    if os.path.exists(SWAP_META_PATH):
        _meta_conn = sqlite3.connect(
            f"file:{SWAP_META_PATH}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        _meta_conn.row_factory = sqlite3.Row
        print(f"  ✅ swap: meta.db loaded")
    else:
        print(f"  ⚠️  swap: meta.db not found at {SWAP_META_PATH}")


def is_available() -> bool:
    """True if the swap index is loaded and queryable."""
    return _lsh is not None and _meta_conn is not None


# ── Query ─────────────────────────────────────────────────────────────────────

def _reconstruct_minhash(gtin: int) -> Optional[object]:
    """
    Rebuild the MinHash for a product from its canonicals blob in meta.db.

    MinHash(num_perm, seed=1) uses a fixed permutation matrix, so this
    produces a hash bit-identical to the one inserted at build time.
    Returns None if the product isn't in meta.db or has no canonicals.
    """
    meta = _get_meta(gtin)
    if meta is None:
        return None
    canon_set = set(_decode_canonicals(meta.get("canonicals")))
    if not canon_set:
        return None
    try:
        from datasketch import MinHash  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    m = MinHash(num_perm=_num_perm)
    for cid in canon_set:
        m.update(cid.to_bytes(2, "little"))
    return m


def _query_sync(
    query_gtin:        int,
    avoid_canonicals:  set[int],
    max_results:       int = 10,
) -> list[dict]:
    """
    Synchronous swap query — called via asyncio.to_thread().

    1. Look up the query product's MinHash in the index.
    2. Ask the LSH for approximate nearest neighbours.
    3. Filter: drop any result whose canonical set intersects avoid_canonicals.
    4. Return ranked alternatives as dicts with gtin + meta fields.
    """
    if not is_available():
        return []

    mh = _reconstruct_minhash(query_gtin)
    if mh is None:
        return []

    try:
        candidates = _lsh.query(mh)  # type: ignore[union-attr]
    except Exception:  # pylint: disable=broad-except
        return []

    # Remove the query product itself
    candidates = [c for c in candidates if c != query_gtin]

    results: list[dict] = []
    for gtin in candidates:
        if len(results) >= max_results:
            break

        # Fetch meta for this candidate
        meta = _get_meta(gtin)
        if meta is None:
            continue

        # Safety filter — drop if any canonical overlaps avoid set
        candidate_canonicals = set(_decode_canonicals(meta.get("canonicals")))
        if candidate_canonicals & avoid_canonicals:
            continue

        results.append({
            "gtin":       gtin,
            "name":       meta.get("name"),
            "nova":       meta.get("nova"),
            "nutriscore": meta.get("nutriscore"),
            "healthstar": meta.get("healthstar"),
        })

    return results


def _get_meta(gtin: int) -> Optional[dict]:
    """Fetch product metadata from meta.db for a candidate GTIN."""
    if _meta_conn is None:
        return {"canonicals": None, "name": None,
                "nova": None, "nutriscore": None, "healthstar": None}
    row = _meta_conn.execute(
        "SELECT name, nova, nutriscore, healthstar, canonicals "
        "FROM product_meta WHERE gtin = ?",
        (gtin,),
    ).fetchone()
    return dict(row) if row else None


def _decode_canonicals(blob: Optional[bytes]) -> list[int]:
    """Decode the LE uint16_t canonicals blob."""
    if not blob:
        return []
    import struct  # pylint: disable=import-outside-toplevel
    count = len(blob) // 2
    return list(struct.unpack_from(f"<{count}H", blob))


async def query(
    query_gtin:       int,
    avoid_canonicals: set[int],
    max_results:      int = 10,
) -> list[dict]:
    """
    Async swap query. Wraps the synchronous LSH search in a thread.

    Args:
        query_gtin:       EAN-13 as integer (the product the user is holding)
        avoid_canonicals: set of canonical IDs the user is sensitive to
        max_results:      maximum number of alternatives to return

    Returns:
        List of product dicts, ranked by LSH similarity (best first).
        Empty list if the index is not loaded or no safe alternatives exist.
    """
    return await asyncio.to_thread(
        _query_sync, query_gtin, avoid_canonicals, max_results
    )
