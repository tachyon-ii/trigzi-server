"""
=============================================================================
Module:        Test — Product endpoint
Location:      tests/test_product_endpoint.py
Description:   Verifies that GET /api/v1/product/<gtin> returns valid,
               parseable JSON — the most basic contract of the product API.

               These are live-server tests. Run against a running Trigzi
               instance (default: http://127.0.0.1:5000).

Environment variables:
    TRIGZI_URL          Base URL (default: http://127.0.0.1:5000)
    TRIGZI_TEST_GTIN    GTIN to use (default: 09310645355078)
=============================================================================
"""

# pylint: disable=missing-function-docstring

import os
import requests

BASE_URL  = os.getenv("TRIGZI_URL",       "http://127.0.0.1:5000")
TEST_GTIN = os.getenv("TRIGZI_TEST_GTIN", "09310645355078")


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------

def test_product_returns_200():
    """Known GTIN must return HTTP 200."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/{TEST_GTIN}", timeout=10)
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}.\n"
        f"Body: {resp.text[:200]!r}"
    )


def test_product_returns_valid_json():
    """Response body must be parseable JSON — not an empty body or HTML error page."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/{TEST_GTIN}", timeout=10)
    try:
        body = resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\n"
            f"Status: {resp.status_code}\n"
            f"Body:   {resp.text[:200]!r}"
        ) from exc
    assert body, "JSON body is empty"


def test_product_has_status_and_product_keys():
    """Top-level envelope must contain 'status' and 'product'."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/{TEST_GTIN}", timeout=10)
    body = resp.json()
    assert "status"  in body, f"Missing 'status' key: {body}"
    assert "product" in body, f"Missing 'product' key: {body}"
    assert body["status"] == "complete", f"Expected status=complete: {body['status']}"


def test_product_fields_are_json_serializable():
    """No field in the product dict may be a raw bytes object."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/{TEST_GTIN}", timeout=10)
    body = resp.json()
    product = body.get("product", {})

    def _check(obj, path="product"):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _check(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _check(v, f"{path}[{i}]")
        elif isinstance(obj, bytes):
            raise AssertionError(
                f"Field {path!r} is a raw bytes object — not JSON-serializable. "
                "The serialiser is missing."
            )

    _check(product)


def test_product_has_required_fields():
    """product dict must have gtin, name, and country_code."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/{TEST_GTIN}", timeout=10)
    product = resp.json().get("product", {})
    for field in ("gtin", "name", "country_code"):
        assert field in product, f"Missing required field {field!r} in product: {product}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_product_unknown_gtin_returns_404():
    """A valid-format GTIN absent from the DB must return 404."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/9999999999994", timeout=10)
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("status") == "not_found", f"Expected status=not_found: {body}"


def test_product_invalid_gtin_returns_400():
    """Non-numeric GTIN must return 400."""
    resp = requests.get(f"{BASE_URL}/api/v1/product/notabarcode", timeout=10)
    assert resp.status_code == 400
