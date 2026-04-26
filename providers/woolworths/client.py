"""
=============================================================================
Module:        Woolworths Client
Location:      providers/woolworths/client.py
Description:   HTTP client for Woolworths Australia's product Search API.
               Uses curl_cffi to impersonate a recent Chrome TLS fingerprint,
               which is required to bypass Akamai's bot-detection layer
               protecting woolworths.com.au.

Architecture Note:
This is the upstream-fetching half of the Woolworths provider plug-in —
its only responsibility is to return raw JSON for one GTIN, or None on
any non-200 response. Field translation lives in
providers/woolworths/formatter.py. The two halves are kept separate so
upstream API changes are isolated to one file at a time.

The TLS-fingerprint impersonation (chrome120) is load-bearing — without
it, Akamai returns 403 immediately. The seed-cookies GET on the homepage
before the search call mirrors what a real browser session does.
=============================================================================
"""

from curl_cffi import requests

IMPERSONATE = "chrome120"
BASE_URL = "https://www.woolworths.com.au"
SEARCH_URL = f"{BASE_URL}/apis/ui/Search/products"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-AU,en;q=0.9",
    "referer": "https://www.woolworths.com.au/",
    "origin": "https://www.woolworths.com.au",
    "x-requested-with": "XMLHttpRequest",
}


def fetch_raw(gtin):
    """Fetch the raw Woolworths product payload for a GTIN. Returns dict or None."""
    session = requests.Session(impersonate=IMPERSONATE)
    session.headers.update(HEADERS)
    session.get(BASE_URL, timeout=10)  # Seed cookies

    params = {"searchTerm": gtin, "pageNumber": 1, "pageSize": 1}
    resp = session.get(SEARCH_URL, params=params, timeout=10)

    if resp.status_code == 200:
        return resp.json()
    return None
