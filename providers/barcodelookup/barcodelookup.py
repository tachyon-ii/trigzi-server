#!/usr/bin/env python3
"""
barcodelookup.py — Plan C fallback GTIN resolver.

Lookup hierarchy:
  A. Woolworths enrichment shard  (data/raw/woolworths/)
  B. Open Food Facts shard        (/var/www/off/)
  C. barcodelookup.com scrape     ← this module
  D. User photo OCR               (last resort — ask user)

Returns a normalised dict matching the OFF schema shape so callers
need no format switching:

    {
        "gtin":            "50819461",
        "source":          "barcodelookup",
        "name":            "Fishermans Friend",
        "brand":           "Fisherman's Friend",
        "category":        "Food, Beverages & Tobacco",
        "image_url":       None,
        "ingredients_raw": "Sweeteners: Sorbitol...",
        "nutrition_100g":  None,   # raw string only — not parsed
        "nutrition_raw":   "Energy 4 kcal, Fat 1 g...",
        "description":     "Fishermans friend. Country of origin: Australia.",
    }

Module usage:
    from utils.barcodelookup import BarcodeLookup
    bl = BarcodeLookup()
    record = bl.get("50819461")   # dict or None

CLI usage:
    ./utils/barcodelookup.py 50819461
    ./utils/barcodelookup.py 50819461 9300617207657
"""

import sys
import re
import time
import random
from typing import Optional, List, List
from curl_cffi import requests
from html import unescape

BASE_URL = "https://www.barcodelookup.com"
IMPERSONATE = "chrome120"


def make_session() -> requests.Session:
    session = requests.Session(impersonate=IMPERSONATE)
    session.headers.update({
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-AU,en;q=0.9",
        "referer": BASE_URL,
    })
    return session


def fetch_page(session: requests.Session, barcode: str) -> Optional[str]:
    # Strip leading zeros for URL (barcodelookup uses the numeric value)
    url = f"{BASE_URL}/{barcode.lstrip('0') or barcode}"
    resp = session.get(url, timeout=15)
    if resp.status_code == 404:
        print(f"  [!] Not found: {barcode}")
        return None
    if resp.status_code == 403:
        print(f"  [!] Blocked (403) for {barcode}")
        return None
    resp.raise_for_status()
    return resp.text


def extract_field(html: str, field_name: str) -> Optional[str]:
    """Extract value="..." from an input field by name attribute."""
    pattern = rf'name="{re.escape(field_name)}"[^>]*value="([^"]*)"'
    m = re.search(pattern, html)
    if not m:
        # Also try value before name
        pattern = rf'value="([^"]*)"[^>]*name="{re.escape(field_name)}"'
        m = re.search(pattern, html)
    return unescape(m.group(1)).strip() if m else None


def extract_textarea(html: str, field_name: str) -> Optional[str]:
    """Extract content from a textarea by name."""
    pattern = rf'<textarea[^>]*name="{re.escape(field_name)}"[^>]*>(.*?)</textarea>'
    m = re.search(pattern, html, re.DOTALL)
    return unescape(m.group(1)).strip() if m else None


def parse_product(html: str, barcode: str) -> dict:
    """Pull the fields we care about from the edit form values."""

    # Title from <h4> (cleaner than the input field)
    title_m = re.search(r'<h4>\s*(.*?)\s*</h4>', html)
    title = unescape(title_m.group(1)).strip() if title_m else None

    # Category shown in product-text span
    cat_m = re.search(
        r'Category:.*?<span class="product-text">(.*?)</span>', html, re.DOTALL)
    category = unescape(cat_m.group(1)).strip() if cat_m else None

    # The edit form has the authoritative structured data
    brand        = extract_field(html, "brand")
    ingredients  = extract_field(html, "ingredients")
    nutrition    = extract_field(html, "nutritionFacts")
    description  = extract_textarea(html, "description")

    # Barcode formats line
    fmt_m = re.search(
        r'Barcode Formats:.*?<span class="product-text">(.*?)</span>', html, re.DOTALL)
    formats = unescape(fmt_m.group(1)).strip() if fmt_m else None

    return {
        "gtin":            barcode,
        "source":          "barcodelookup",
        "name":            title or "Unknown Product",
        "brand":           brand or "",
        "category":        category or "",
        "image_url":       None,
        "ingredients_raw": ingredients or "",
        "nutrition_100g":  None,   # not parsed — raw string only
        "nutrition_raw":   nutrition or "",
        "description":     description or "",
        "formats":         formats or "",
    }


class BarcodeLookup:
    """
    Module-level singleton API for Plan C GTIN resolution.
    Maintains a session across calls to amortise connection overhead.

    Usage:
        from utils.barcodelookup import BarcodeLookup
        bl = BarcodeLookup()
        record = bl.get("50819461")   # dict or None
    """

    def __init__(self):
        self._session = make_session()

    def get(self, gtin: str) -> Optional[dict]:
        """
        Look up a single GTIN. Returns normalised dict or None if not found.
        Includes a random 1-2s polite delay to avoid hammering the site.
        """
        time.sleep(random.uniform(1.0, 2.0))
        html = fetch_page(self._session, gtin)
        if html is None:
            return None
        return parse_product(html, gtin)

    def get_many(self, gtins: List[str]) -> list:
        """Look up multiple GTINs with polite delays between requests."""
        return lookup(gtins)


def lookup(barcodes: List[str]) -> list:
    session = make_session()
    results = []

    for i, barcode in enumerate(barcodes):
        if i > 0:
            delay = random.uniform(2.0, 4.0)
            print(f"\n  [*] Waiting {delay:.1f}s...")
            time.sleep(delay)

        print(f"\n{'='*52}")
        print(f"  barcodelookup.com → {barcode}")
        print(f"{'='*52}")

        html = fetch_page(session, barcode)
        if html is None:
            results.append({"barcode": barcode, "error": "not found"})
            continue

        product = parse_product(html, barcode)
        results.append(product)

        # Pretty print
        for k, v in product.items():
            if v and k not in ("source", "image_url", "nutrition_100g"):
                print(f"  {k:<14}: {v}")

    return results


# Module-level singleton — import this, not the class directly:
#   from utils.barcodelookup import lookup_client
#   record = lookup_client.get("50819461")
lookup_client = BarcodeLookup()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Look up a GTIN via barcodelookup.com (Plan C fallback)."
    )
    parser.add_argument("gtins", nargs="+", help="One or more GTINs to look up")
    args = parser.parse_args()
    lookup(args.gtins)
