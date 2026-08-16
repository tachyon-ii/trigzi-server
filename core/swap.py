"""
=============================================================================
Module:        Swap Query Client (Qdrant)
Location:      core/swap.py
Description:   Finds structurally similar but allergen-safe product
               alternatives using a Qdrant sparse-vector collection.

Architecture:
    The swap index lives entirely inside the Qdrant process (Rust, native C).
    Hypercorn workers are stateless — they hold nothing but an async HTTP
    client pointing at Qdrant. There is no in-process index, no pickle, no
    shared-memory headache, and no GIL contention on queries.

Encoding:
    Each product is stored as a sparse binary vector over the
    canonical-ingredient vocabulary (~2,500 dimensions):

        indices: [14, 102, 2405]
        values:  [1.0, 1.0, 1.0]

    Dot-product similarity = count of shared canonical IDs ≈ Jaccard overlap.

Allergen filtering:
    The avoid_canonicals set is translated into a Qdrant MustNot filter and
    applied inside Qdrant's HNSW graph traversal — guarantees returned
    products never contain any allergen the user is sensitive to, with no
    Python post-filtering required.

Payload per point (stored in Qdrant, returned with results):
    canonical_ids  — list[int] (indexed; used by the pre-filter)
    name           — product display name
    nova           — NOVA group (1–4)
    nutriscore     — NutriScore tier (0–4)
    healthstar     — Health Star Rating (1–10)

Environment variables:
    QDRANT_URL         default: http://localhost:6333
    QDRANT_API_KEY     optional (Qdrant Cloud only)
    QDRANT_COLLECTION  default: swap
=============================================================================
"""

from __future__ import annotations

import os
import struct
from typing import Optional

QDRANT_URL         = os.environ.get("QDRANT_URL",        "http://localhost:6333")
QDRANT_API_KEY     = os.environ.get("QDRANT_API_KEY",    None)
QDRANT_COLLECTION  = os.environ.get("QDRANT_COLLECTION", "swap")
SPARSE_VECTOR_NAME = "canonical"

_client: Optional[object] = None   # AsyncQdrantClient — typed as object to avoid
                                    # import-time dependency if qdrant-client missing


# ── Lifecycle ────────────────────────────────────────────────────────────────

async def connect() -> None:
    """
    Create the Qdrant async HTTP client and verify the collection exists.
    Call once per worker from Quart's before_serving hook.
    Degrades gracefully: if Qdrant is unreachable the swap endpoint
    returns 503 rather than crashing the worker.
    """
    global _client  # pylint: disable=global-statement
    try:
        from qdrant_client import AsyncQdrantClient  # type: ignore
    except ImportError:
        print("  ⚠️  swap: qdrant-client not installed — /swap endpoint disabled")
        return

    client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    try:
        info = await client.get_collection(QDRANT_COLLECTION)
        print(f"  ✅ swap: Qdrant '{QDRANT_COLLECTION}' — {info.points_count:,} points")
        _client = client
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  ⚠️  swap: Qdrant unreachable at {QDRANT_URL} — {exc}")
        print(f"       /swap will return 503 until Qdrant is available")
        await client.close()


async def disconnect() -> None:
    """Close the async HTTP client. Call from Quart's after_serving hook."""
    global _client  # pylint: disable=global-statement
    if _client is not None:
        await _client.close()  # type: ignore[union-attr]
        _client = None


def is_available() -> bool:
    """True when the Qdrant client is connected and the collection is reachable."""
    return _client is not None


# ── Query ────────────────────────────────────────────────────────────────────

def _decode_canonicals(blob: Optional[bytes]) -> list[int]:
    """Decode a gtin_cache.db canonicals blob (packed LE uint16_t[]) → list.

    Deduplicates: Qdrant requires unique indices in a sparse vector, and some
    products carry repeated canonical IDs in their blob (pipeline artefact).
    """
    if not blob:
        return []
    count = len(blob) // 2
    return list(dict.fromkeys(struct.unpack_from(f"<{count}H", blob)))


async def query(
    query_gtin:       int,
    avoid_canonicals: set[int],
    max_results:      int = 10,
) -> Optional[list[dict]]:
    """
    Find similar-but-safe product alternatives via Qdrant sparse vector search.

    The query product's canonical IDs are looked up from the local SQLite store
    (already loaded in each worker, O(1) lookup, no network hop). Qdrant then
    executes the sparse dot-product search with an inline allergen pre-filter.

    Args:
        query_gtin:       EAN-13 as integer (the product the user is holding)
        avoid_canonicals: set of canonical IDs the user must avoid
        max_results:      maximum alternatives to return (default 10, cap 20)

    Returns:
        List of product dicts ranked by ingredient overlap (best first).
        None  — query GTIN is not in our database.
        []    — GTIN known but no safe alternatives found.
    """
    if not _client:
        return []

    from qdrant_client.models import (  # pylint: disable=import-outside-toplevel
        Filter,
        FieldCondition,
        MatchAny,
        SparseVector,
    )
    from core import sqlite_store  # pylint: disable=import-outside-toplevel

    # ── Resolve query product's canonical IDs from local SQLite ──────────────
    product = sqlite_store.get_product(query_gtin)
    if product is None:
        return None   # GTIN not in gtin_cache.db

    canon_ids = _decode_canonicals(product.get("canonicals"))
    if not canon_ids:
        return []     # product has no canonical ingredients — cannot find neighbours

    # ── Build the allergen pre-filter ─────────────────────────────────────────
    # Applied inside Qdrant's HNSW traversal: guaranteed safe results, not
    # a Python post-filter applied after retrieval.
    query_filter: Optional[Filter] = None
    if avoid_canonicals:
        query_filter = Filter(
            must_not=[
                FieldCondition(
                    key="canonical_ids",
                    match=MatchAny(any=list(avoid_canonicals)),
                )
            ]
        )

    # ── Sparse dot-product search ─────────────────────────────────────────────
    # score = Σ (query_value × candidate_value) for shared indices
    #       = count of shared canonical IDs  (since all values are 1.0)
    # score_threshold=1.0 ensures at least one shared ingredient.
    # qdrant-client ≥1.9 uses query_points(); search() was removed in 1.19.
    try:
        response = await _client.query_points(  # type: ignore[union-attr]
            collection_name=QDRANT_COLLECTION,
            query=SparseVector(
                indices=canon_ids,
                values=[1.0] * len(canon_ids),
            ),
            using=SPARSE_VECTOR_NAME,
            query_filter=query_filter,
            limit=max_results + 5,   # slight over-fetch in case self appears
            with_payload=["name", "nova", "nutriscore", "healthstar"],
            score_threshold=1.0,
        )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  [!] swap: Qdrant search error: {exc}")
        return []

    hits = response.points  # QueryResponse wraps results in .points

    # ── Build response ────────────────────────────────────────────────────────
    results: list[dict] = []
    for hit in hits:
        if int(hit.id) == query_gtin:
            continue   # skip self (should not appear given pre-filter, but be safe)
        results.append({
            "gtin":       hit.id,
            "name":       hit.payload.get("name"),
            "nova":       hit.payload.get("nova"),
            "nutriscore": hit.payload.get("nutriscore"),
            "healthstar": hit.payload.get("healthstar"),
        })
        if len(results) >= max_results:
            break

    return results
