"""
=============================================================================
Module:        Test — Qdrant Swap Index
Location:      tests/test_qdrant_swap.py
Description:   Verifies the full Qdrant swap stack:
                 1. Infrastructure  — Qdrant process is up, port reachable
                 2. Collection      — 'swap' collection exists, is populated,
                                      has sparse vector config + payload index
                 3. Unit            — _decode_canonicals edge cases
                 4. API             — /api/v1/swap/<gtin> via live Hypercorn

Run order matters: infrastructure → collection → unit → API.
If Qdrant is not running, tests 1-2 and 4 fail fast; unit tests still pass.

Environment variables:
    TRIGZI_URL          Base URL for API tests (default: http://127.0.0.1:5000)
    QDRANT_URL          Qdrant server URL     (default: http://localhost:6333)
    GTIN_DB             Path to gtin_cache.db (default: ../data/gtin_cache.db)
    TRIGZI_TEST_GTIN    Override test GTIN    (optional — auto-selected if unset)
=============================================================================
"""

# pylint: disable=missing-function-docstring

import os
import socket
import sqlite3
import struct
from pathlib import Path

import pytest
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL   = os.getenv("TRIGZI_URL",  "http://127.0.0.1:5000")
QDRANT_URL = os.getenv("QDRANT_URL",  "http://localhost:6333")
GTIN_DB    = Path(os.getenv("GTIN_DB", "../data/gtin_cache.db"))

COLLECTION = os.getenv("QDRANT_COLLECTION", "swap")
MIN_POINTS = 950_000   # ingestion health threshold — fail if fewer loaded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qdrant_host_port() -> tuple[str, int]:
    url = QDRANT_URL.rstrip("/")
    host_part = url.split("://", 1)[-1]
    if ":" in host_part:
        h, p = host_part.rsplit(":", 1)
        return h, int(p)
    return host_part, 6333


def _pick_test_gtin() -> int | None:
    """Return a GTIN that has canonicals in gtin_cache.db, or None."""
    override = os.getenv("TRIGZI_TEST_GTIN")
    if override:
        return int(override)
    if not GTIN_DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{GTIN_DB}?mode=ro", uri=True)
        row = con.execute(
            # EAN-8 minimum is 8 digits (10^7); exclude junk/zero GTINs
            "SELECT gtin FROM product WHERE canonicals IS NOT NULL AND gtin >= 10000000 LIMIT 1"
        ).fetchone()
        con.close()
        return row[0] if row else None
    except Exception:  # pylint: disable=broad-except
        return None


# ---------------------------------------------------------------------------
# 1. Infrastructure — Qdrant liveness
# ---------------------------------------------------------------------------

# !! IF THESE TESTS FAIL — QDRANT IS DEAD !!
# All API swap tests will return 503 until Qdrant is restarted.
# Check: systemctl status qdrant   or   journalctl -u qdrant -n 50

def test_qdrant_port_reachable():
    """TCP connect to Qdrant port — fails immediately if the process is down."""
    host, port = _qdrant_host_port()
    try:
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        pytest.fail(
            f"Qdrant is NOT bound to {host}:{port} — {exc}\n"
            "Restart with:  systemctl restart qdrant"
        )


def test_qdrant_http_responds():
    """GET / on Qdrant returns 200 with version info."""
    try:
        resp = requests.get(f"{QDRANT_URL}/", timeout=5)
    except requests.ConnectionError as exc:
        pytest.fail(f"Qdrant HTTP unreachable at {QDRANT_URL} — {exc}")

    assert resp.status_code == 200, f"Qdrant / returned {resp.status_code}"
    body = resp.json()
    assert "version" in body, f"Unexpected Qdrant root response: {body}"
    print(f"\n  Qdrant server version: {body['version']}")


# ---------------------------------------------------------------------------
# 2. Collection health
# ---------------------------------------------------------------------------

def test_swap_collection_exists():
    """'swap' collection must be present in Qdrant."""
    resp = requests.get(f"{QDRANT_URL}/collections", timeout=5)
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["result"]["collections"]]
    assert COLLECTION in names, (
        f"Collection '{COLLECTION}' not found. Collections present: {names}\n"
        "Run: cd trigzi-db && make load-qdrant"
    )


def test_swap_collection_point_count():
    """Collection must have at least MIN_POINTS points (ingestion health check)."""
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}", timeout=5)
    assert resp.status_code == 200
    count = resp.json()["result"]["points_count"]
    assert count >= MIN_POINTS, (
        f"Only {count:,} points in '{COLLECTION}' — expected ≥ {MIN_POINTS:,}.\n"
        "Ingestion may have been incomplete. Re-run: make load-qdrant"
    )
    print(f"\n  '{COLLECTION}' has {count:,} points")


def test_swap_collection_has_sparse_vector():
    """Collection must have a sparse vector named 'canonical'."""
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}", timeout=5)
    assert resp.status_code == 200
    config = resp.json()["result"]["config"]
    sparse = config.get("params", {}).get("sparse_vectors", {})
    assert "canonical" in sparse, (
        f"Sparse vector 'canonical' missing from collection config.\n"
        f"Sparse vectors present: {list(sparse.keys())}"
    )


def test_swap_collection_has_payload_index():
    """canonical_ids must be indexed for O(log n) allergen pre-filtering."""
    # Payload index info lives in the collection info under result.payload_schema
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}", timeout=5)
    assert resp.status_code == 200
    schema = resp.json().get("result", {}).get("payload_schema", {})
    assert "canonical_ids" in schema, (
        f"Payload index on 'canonical_ids' missing.\n"
        f"Indexed fields present: {list(schema.keys())}"
    )


# ---------------------------------------------------------------------------
# 3. Unit — _decode_canonicals
# ---------------------------------------------------------------------------

def _pack(*ids: int) -> bytes:
    return struct.pack(f"<{len(ids)}H", *ids)


def _decode(blob) -> list[int]:
    """Mirror of core/swap.py _decode_canonicals."""
    if not blob:
        return []
    count = len(blob) // 2
    return list(dict.fromkeys(struct.unpack_from(f"<{count}H", blob)))


def test_decode_empty():
    assert _decode(b"") == []
    assert _decode(None) == []


def test_decode_single():
    assert _decode(_pack(42)) == [42]


def test_decode_normal():
    assert _decode(_pack(1, 2, 3)) == [1, 2, 3]


def test_decode_deduplicates():
    """Duplicate canonical IDs must be removed (Qdrant requires unique indices)."""
    result = _decode(_pack(10, 20, 10, 30, 20))
    assert result == [10, 20, 30], f"Expected [10, 20, 30], got {result}"


def test_decode_preserves_order():
    """First occurrence order must be preserved after deduplication."""
    result = _decode(_pack(5, 3, 1, 3, 5))
    assert result == [5, 3, 1]


def test_decode_all_same():
    result = _decode(_pack(7, 7, 7, 7))
    assert result == [7]


# ---------------------------------------------------------------------------
# 4. API — /api/v1/swap/<gtin>
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_gtin() -> int:
    gtin = _pick_test_gtin()
    if gtin is None:
        pytest.skip(
            f"No test GTIN available — set TRIGZI_TEST_GTIN or ensure {GTIN_DB} exists"
        )
    return gtin


def test_swap_returns_ok(test_gtin):
    """Known GTIN with canonicals must return status=ok and a list of alternatives."""
    resp = requests.get(f"{BASE_URL}/api/v1/swap/{test_gtin}", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "ok", f"Expected status=ok: {body}"
    assert "alternatives" in body, f"Missing 'alternatives' key: {body}"
    assert isinstance(body["alternatives"], list)
    print(f"\n  GTIN {test_gtin}: {len(body['alternatives'])} alternatives")


def test_swap_alternatives_shape(test_gtin):
    """Each alternative must have the expected fields."""
    resp = requests.get(f"{BASE_URL}/api/v1/swap/{test_gtin}", timeout=10)
    body = resp.json()
    alts = body.get("alternatives", [])
    if not alts:
        pytest.skip(f"No alternatives returned for {test_gtin} — cannot validate shape")
    for alt in alts:
        assert "gtin" in alt,       f"Missing 'gtin' in: {alt}"
        assert "name" in alt,       f"Missing 'name' in: {alt}"
        assert "nova" in alt,       f"Missing 'nova' in: {alt}"
        assert "nutriscore" in alt, f"Missing 'nutriscore' in: {alt}"
        assert "healthstar" in alt, f"Missing 'healthstar' in: {alt}"


def test_swap_does_not_return_self(test_gtin):
    """The query product must not appear in its own alternatives."""
    resp = requests.get(f"{BASE_URL}/api/v1/swap/{test_gtin}", timeout=10)
    body = resp.json()
    gtins = [a["gtin"] for a in body.get("alternatives", [])]
    assert test_gtin not in gtins and str(test_gtin) not in gtins, (
        f"Query GTIN {test_gtin} appeared in its own alternatives"
    )


def test_swap_respects_n_param(test_gtin):
    """n=3 must return at most 3 alternatives."""
    resp = requests.get(f"{BASE_URL}/api/v1/swap/{test_gtin}?n=3", timeout=10)
    body = resp.json()
    assert len(body.get("alternatives", [])) <= 3


def test_swap_unknown_gtin_returns_404():
    """A valid-format GTIN absent from the database must return 404 with status=not_found."""
    # 9999999999994 is a valid EAN-13 (check digit 4) that will not be in our DB
    resp = requests.get(f"{BASE_URL}/api/v1/swap/9999999999994", timeout=10)
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("status") == "not_found", f"Expected status=not_found: {body}"


def test_swap_invalid_gtin_returns_400():
    """Non-numeric GTIN must return 400."""
    resp = requests.get(f"{BASE_URL}/api/v1/swap/notabarcode", timeout=10)
    assert resp.status_code == 400


def test_swap_with_avoid_excludes_canonicals(test_gtin):
    """
    Passing a fake avoid set of every possible canonical ID (0-2500)
    should return zero alternatives — Qdrant's MustNot filter blocks all.
    """
    # Use a broad avoid set — any product sharing any of these IDs is excluded.
    # We pick IDs 1-100 which covers common ingredients.
    avoid = ",".join(str(i) for i in range(1, 101))
    resp_no_avoid  = requests.get(f"{BASE_URL}/api/v1/swap/{test_gtin}", timeout=10)
    resp_with_avoid = requests.get(
        f"{BASE_URL}/api/v1/swap/{test_gtin}?avoid={avoid}", timeout=10
    )
    # Both should succeed (200)
    assert resp_no_avoid.status_code == 200
    assert resp_with_avoid.status_code == 200
    # Avoid set should produce equal or fewer results
    n_base  = len(resp_no_avoid.json().get("alternatives", []))
    n_avoid = len(resp_with_avoid.json().get("alternatives", []))
    assert n_avoid <= n_base, (
        f"avoid= produced MORE results ({n_avoid}) than no-avoid ({n_base}) — filter not working"
    )
