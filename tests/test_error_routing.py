import os
import socket
import pytest
import requests

# Default to localhost, but allow CI/CD to override with the live domain
BASE_URL = os.getenv("TRIGZI_URL", "https://trigzi.com")

# ---------------------------------------------------------------------------
# Hypercorn liveness check
# ---------------------------------------------------------------------------
# test_hypercorn_alive() below verifies that Hypercorn is bound and accepting
# TCP connections on 127.0.0.1:5000.  Run it first — if it fails, ALL of the
# /api/ tests will return 502 (Nginx cannot reach the upstream) and the
# failures are meaningless.
#
# QUICK DIAGNOSIS:
#   If test_api_404_returns_json or test_api_500_returns_json returns 502,
#   Hypercorn is dead.  Check:
#       journalctl -u trigzi_api -n 100 --no-pager
#       tail -n 100 /var/www/trigzi/logs/api.log
# ---------------------------------------------------------------------------

def test_hypercorn_alive():
    """
    Verify Hypercorn is bound to 127.0.0.1:5000 before any API tests run.

    A TCP connection to port 5000 is cheaper and more direct than an HTTP
    request: it passes even if the app is still in startup, and it fails
    immediately (rather than after Nginx's proxy_read_timeout) if Hypercorn
    is dead.

    IF THIS TEST FAILS: Hypercorn is not running.  All /api/ tests will 502.
    Restart with:  sudo systemctl restart trigzi_api
    """
    try:
        sock = socket.create_connection(("127.0.0.1", 5000), timeout=3)
        sock.close()
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        pytest.fail(
            f"Hypercorn is NOT bound to 127.0.0.1:5000 — {exc}\n"
            "All /api/ tests will return 502 until it is restarted."
        )


def test_web_404_returns_html():
    """
    Simulate a browser requesting a non-existent web page.
    Expects the custom Trigzi 404 HTML page.
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    
    # Hit a missing route at the root level
    response = requests.get(f"{BASE_URL}/flubber2u2", headers=headers, verify=False)
    
    # Assert Nginx intercepted and returned the correct status and type
    assert response.status_code == 404
    assert "text/html" in response.headers.get("Content-Type", "")
    
    # Assert it served our specific custom page, not the Nginx default
    assert "404 Error" in response.text
    assert "The coordinates you requested lead to empty space." in response.text
    assert "box__ghost" in response.text


# ---------------------------------------------------------------------------
# !! IF THESE TWO TESTS RETURN 502 — HYPERCORN IS DEAD !!
#
# test_api_404_returns_json and test_api_500_returns_json both route through
# Nginx → Hypercorn.  A 502 means Nginx reached the upstream but got no
# response — i.e. Hypercorn is not running on port 5000.
#
# test_hypercorn_alive() at the top of this file catches this earlier and
# gives a clearer error message, but 502 here is the definitive symptom.
# ---------------------------------------------------------------------------

def test_api_404_returns_json():
    """
    Simulate an API client requesting a non-existent endpoint.
    Expects a strict JSON payload.
    """
    headers = {
        "Accept": "application/json"
    }
    
    # Hit a missing route inside the /api/ block
    response = requests.get(f"{BASE_URL}/api/flubber2u2", headers=headers, verify=False)
    
    # Assert Nginx intercepted and returned the correct status and type
    assert response.status_code == 404
    assert "application/json" in response.headers.get("Content-Type", "")
    
    # Assert the JSON structure matches the @json_404 block in trigzi.com.conf
    data = response.json()
    assert data.get("code") == 404
    assert data.get("error") == "Not Found"


def test_api_500_returns_json():
    """
    Optional: If you have a specific endpoint designed to throw a 500 for testing,
    you can uncomment and point this test at it to verify the 500 JSON handler.
    """
    response = requests.get(f"{BASE_URL}/api/trigger-500", verify=False)
    assert response.status_code == 500
    assert "application/json" in response.headers.get("Content-Type", "")
    data = response.json()
    assert data.get("code") == 500
    assert data.get("error") == "Internal Server Error"

def test_root_404_returns_html_by_default():
    """
    Simulate a browser requesting a missing page at the root.
    Expects the custom Trigzi 404 HTML page.
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    response = requests.get(f"{BASE_URL}/phantom-page", headers=headers, verify=False)
    
    assert response.status_code == 404
    assert "text/html" in response.headers.get("Content-Type", "")
    
    # Assert it served the specific custom HTML page
    assert "404 Error" in response.text
    assert "box__ghost" in response.text


def test_root_404_returns_json_when_requested():
    """
    Simulate a headless client or XHR request hitting a missing page at the root
    while explicitly asking for JSON. 
    Expects the Nginx content-negotiated JSON payload.
    """
    headers = {
        "Accept": "application/json"
    }
    
    response = requests.get(f"{BASE_URL}/phantom-page", headers=headers, verify=False)
    
    # This will fail until the Nginx $err_ext map is deployed
    assert response.status_code == 404
    assert "application/json" in response.headers.get("Content-Type", "")
    
    # If it receives HTML, this .json() call will throw a JSONDecodeError
    data = response.json()
    assert data.get("code") == 404
    assert data.get("error") == "Not Found"
