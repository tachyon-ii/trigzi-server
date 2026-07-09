import os
import pytest
import requests

# Default to localhost, but allow CI/CD to override with the live domain
BASE_URL = os.getenv("TRIGZI_URL", "https://trigzi.com")

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
