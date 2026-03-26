#!/usr/bin/env python3
from __future__ import annotations
"""
core/data_manager.py

Product lookup and enrichment orchestrator.

Case 1: Record in DB, _enrichment_llm set   → return immediately
Case 2: Record in DB, not enriched           → enrich via LLM, return enriched
Case 3: Not in DB                            → return None → 404 → client offers OCR

Debug flags (set in environment):
    DEBUG_FORCE_NOT_FOUND=1   — always return None (simulate Case 3)
    DEBUG_FORCE_UNENRICHED=1  — strip enrichment, simulate Case 2
"""

import os
import re
import json
import time
import random
import urllib.request
import urllib.error
from socket import timeout as SocketTimeout
from typing import Optional

from utils.off_lookup import OFFLookup
from core.telemetry import log_scan

# --- Config ---

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL   = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={{api_key}}"
)

VALIDATE_JSONL = "/var/www/trigzi/data/validate.jsonl"

NON_FOOD_CATEGORIES = {
    "Cleaning & Laundry",
    "Home & Garden",
    "Health & Beauty",
    "Pet",
    "Tobacco",
}

# Debug flags
DEBUG_FORCE_NOT_FOUND   = os.environ.get("DEBUG_FORCE_NOT_FOUND",   "0") == "1"
DEBUG_FORCE_UNENRICHED  = os.environ.get("DEBUG_FORCE_UNENRICHED",  "0") == "1"

_off     = OFFLookup()
_api_key = os.environ.get("GEMINI_API_KEY", "")


# --- GTIN helpers ---

def _variations(gtin: str) -> list:
    v = [gtin]
    if gtin.startswith('0'):
        v.append(gtin.lstrip('0'))
    if len(gtin) == 12:
        v.append('0' + gtin)
    return list(dict.fromkeys(v))


# --- Validate queue ---

def _queue_for_validation(record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(VALIDATE_JSONL), exist_ok=True)
        with open(VALIDATE_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f"  [!] validate queue write failed: {e}")


# --- LLM enrichment ---

def _build_payload(record: dict) -> dict:
    enrichment_schema = {
        "type": "OBJECT",
        "properties": {
            "estimated_health_star": {"type": "NUMBER",  "nullable": True},
            "fodmap_rating":         {"type": "INTEGER"},
            "coeliac_rating":        {"type": "INTEGER"},
            "histamine_rating":      {"type": "INTEGER"},
            "allergen_warnings":     {"type": "ARRAY", "items": {"type": "STRING"}},
            "health_summary":        {"type": "STRING"},
        },
        "required": ["fodmap_rating", "coeliac_rating", "histamine_rating",
                     "allergen_warnings", "health_summary"]
    }

    prompt = (
        f"You are a clinical dietary data extractor.\n"
        f"Analyse this product based strictly on its ingredients and nutrition.\n\n"
        f"Product: {record.get('name', '')}\n"
        f"Ingredients: {record.get('raw_ingredients', '')}\n"
        f"Nutrition per 100g: {json.dumps(record.get('nutrition_100g'))}\n"
        f"Existing Health Star: {record.get('health_star_rating')}\n\n"
        f"If existing_health_star is provided, return null for estimated_health_star.\n"
        f"Return ONLY valid JSON matching the schema. No markdown."
    )

    return {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema":   enrichment_schema,
            "temperature":      0.1
        }
    }


def _call_gemini(record: dict) -> Optional[dict]:
    if not _api_key:
        print("  [!] GEMINI_API_KEY not set")
        return None

    url  = GEMINI_URL.format(api_key=_api_key)
    data = json.dumps(_build_payload(record)).encode('utf-8')
    req  = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'}
    )

    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode('utf-8'))
                text = (body
                        .get('candidates', [{}])[0]
                        .get('content', {})
                        .get('parts', [{}])[0]
                        .get('text', ''))
                return json.loads(text)

        except urllib.error.HTTPError as e:
            if e.code == 429:
                sleep = (2 ** attempt) + random.uniform(1.0, 5.0)
                print(f"  [~] 429 rate limit, sleeping {sleep:.1f}s")
                time.sleep(sleep)
            else:
                print(f"  [!] Gemini HTTP {e.code}: {e.read().decode()[:200]}")
                time.sleep((2 ** attempt) + random.uniform(0, 1.0))

        except (urllib.error.URLError, SocketTimeout, json.JSONDecodeError) as e:
            print(f"  [!] Gemini error attempt {attempt+1}: {e}")
            time.sleep((2 ** attempt) + random.uniform(0, 1.0))

    return None


def _nop_profile() -> dict:
    return {
        "estimated_health_star": None,
        "fodmap_rating":         -1,
        "coeliac_rating":        -1,
        "histamine_rating":      -1,
        "allergen_warnings":     [],
        "health_summary":        "Non-food item. No clinical gut health profile applies."
    }


def enrich(record: dict) -> dict:
    enriched = dict(record)

    if record.get("category") in NON_FOOD_CATEGORIES:
        enriched["clinical_profile"] = _nop_profile()
        enriched["_enrichment_llm"]  = "NOP"
    else:
        log_scan(
            gtin   = record.get("gtin", ""),
            source = record.get("_source_name", "off"),
            text   = record.get("raw_ingredients", "")
        )
        profile = _call_gemini(record)
        if profile:
            enriched["clinical_profile"] = profile
            enriched["_enrichment_llm"]  = GEMINI_MODEL
        else:
            enriched["_enrichment_llm"]  = "FAILED"

    _queue_for_validation(enriched)

    # Write enriched record back to DB
    _off.save(enriched)

    return enriched


# --- Main product lookup ---

def get_product(scanned_gtin: str) -> Optional[dict]:
    """
    Case 1: Enriched record  → return immediately
    Case 2: Unenriched record → caller streams enrichment
    Case 3: Not found         → return None
    """

    # Debug: always return not found
    if DEBUG_FORCE_NOT_FOUND:
        print(f"  [DEBUG] DEBUG_FORCE_NOT_FOUND — returning None for {scanned_gtin}")
        return None

    for candidate in _variations(scanned_gtin):
        record = _off.get(candidate)
        if record:
            # Debug: strip enrichment to simulate Case 2
            if DEBUG_FORCE_UNENRICHED:
                print(f"  [DEBUG] DEBUG_FORCE_UNENRICHED — stripping enrichment for {candidate}")
                record = dict(record)
                record["_enrichment_llm"] = None
                record["clinical_profile"] = None
            return record

    return None


def is_enriched(record: dict) -> bool:
    return bool(record.get("_enrichment_llm"))


# --- OCR product analysis ---

def analyse_product(
    gtin:           str,
    text_front:     str,
    text_nutrition: str,
) -> Optional[dict]:
    if not text_front and not text_nutrition:
        return None

    record = {
        "gtin":            gtin,
        "name":            text_front[:80],
        "raw_ingredients": text_nutrition,
        "nutrition_100g":  None,
        "_enrichment_llm": None,
    }

    profile = _call_gemini(record)
    if not profile:
        return None

    return {
        "type":  "product",
        "items": [{
            "name":               text_front[:80] if text_front else gtin,
            "safe":               profile.get("fodmap_rating", -1) <= 1,
            "verdict":            _verdict(profile),
            "summary":            profile.get("health_summary", ""),
            "warnings":           profile.get("allergen_warnings", []),
            "ingredients":        _parse_ingredients(text_nutrition),
            "flaggedIngredients": profile.get("allergen_warnings", []),
            "detailedReason":     profile.get("health_summary", ""),
        }]
    }


def _parse_ingredients(text: str) -> list:
    """Strip leading label then split on comma, trimming whitespace."""
    text = re.sub(r'^\s*Ingredients?\s*:\s*', '', text, flags=re.IGNORECASE)
    return [i.strip() for i in text.split(',') if i.strip()]


def _verdict(profile: dict) -> str:
    ratings = [
        profile.get("fodmap_rating",    -1),
        profile.get("coeliac_rating",   -1),
        profile.get("histamine_rating", -1),
    ]
    max_r = max((r for r in ratings if r >= 0), default=-1)
    if max_r >= 3: return "Avoid"
    if max_r >= 2: return "Caution"
    if max_r >= 1: return "Low Risk"
    if max_r == 0: return "Safe"
    return "Unknown"
