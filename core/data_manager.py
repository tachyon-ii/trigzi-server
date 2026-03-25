from __future__ import annotations
"""
core/data_manager.py

Product lookup and enrichment orchestrator.

Case 1: Record in OFF tree, _enrichment_llm set   → return immediately
Case 2: Record in OFF tree, not enriched           → return partial, enrich, return enriched
Case 3: Not in OFF tree                            → return None → 404 → client offers OCR
"""

import os
from typing import Optional
import json
import asyncio
import time
import urllib.request
import urllib.error
import random
from socket import timeout as SocketTimeout
from utils.off_lookup import OFFLookup
from core.telemetry import log_scan

# --- Configuration ---

OFF_DIR       = "/var/www/off"
VALIDATE_JSONL = "/var/www/trigzi/data/validate.jsonl"

GEMINI_MODEL  = "gemini-2.5-flash"
GEMINI_URL    = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={{api_key}}"
)

# Non-food categories — skip LLM, stamp NOP immediately
NON_FOOD_CATEGORIES = {
    "Cleaning & Laundry",
    "Home & Garden",
    "Health & Beauty",
    "Pet",
    "Tobacco",
}

# --- Module-level singletons ---

_off     = OFFLookup(OFF_DIR)
_api_key = os.environ.get("GEMINI_API_KEY", "")


# --- GTIN helpers ---

def _variations(gtin: str) -> list:
    """Common retail GTIN permutations."""
    v = [gtin]
    if gtin.startswith('0'):
        v.append(gtin.lstrip('0'))
    if len(gtin) == 12:
        v.append('0' + gtin)
    return list(dict.fromkeys(v))


# --- Validation queue ---

def _queue_for_validation(record: dict) -> None:
    """Append enriched record to validate.jsonl for human review."""
    try:
        os.makedirs(os.path.dirname(VALIDATE_JSONL), exist_ok=True)
        with open(VALIDATE_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f"  [!] validate queue write failed: {e}")


# --- LLM enrichment ---

def _build_enrichment_payload(record: dict) -> dict:
    """Build Gemini API payload for clinical_profile enrichment."""
    enrichment_schema = {
        "type": "OBJECT",
        "properties": {
            "estimated_health_star": {
                "type": "NUMBER",
                "nullable": True,
                "description": "Health Star Rating 0.5-5.0 in 0.5 increments. null if unable to estimate."
            },
            "fodmap_rating": {
                "type": "INTEGER",
                "description": "-1 Unknown, 0 None/Safe, 1 Low, 2 Medium/Portion-dependent, 3 High."
            },
            "coeliac_rating": {
                "type": "INTEGER",
                "description": "-1 Unknown, 0 Safe, 1 Cross-contamination risk, 2 Low-level, 3 High (Wheat/Rye/Barley)."
            },
            "histamine_rating": {
                "type": "INTEGER",
                "description": "-1 Unknown, 0 Safe/Fresh, 1 Low, 2 Moderate/Liberator, 3 High (Aged/Fermented/Cured)."
            },
            "allergen_warnings": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Top 8 allergens present. Empty array if none."
            },
            "health_summary": {
                "type": "STRING",
                "description": "1-2 sentence gut health summary based strictly on ingredients."
            }
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
    """
    Call Gemini API for clinical_profile enrichment.
    Retries up to 8 times with exponential backoff + jitter.
    Returns parsed clinical_profile dict or None on failure.
    """
    if not _api_key:
        print("  [!] GEMINI_API_KEY not set")
        return None

    url     = GEMINI_URL.format(api_key=_api_key)
    payload = _build_enrichment_payload(record)
    data    = json.dumps(payload).encode('utf-8')
    req     = urllib.request.Request(
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
    """Clinical profile for non-food items — no LLM needed."""
    return {
        "estimated_health_star": None,
        "fodmap_rating":         -1,
        "coeliac_rating":        -1,
        "histamine_rating":      -1,
        "allergen_warnings":     [],
        "health_summary":        "Non-food item. No clinical gut health profile applies."
    }


def enrich(record: dict) -> dict:
    """
    Enrich a record with clinical_profile.
    Non-food categories get NOP instantly.
    Food items call Gemini.
    Result is queued to validate.jsonl and returned.
    """
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
            # Enrichment failed — mark as attempted so we don't retry on every scan
            enriched["_enrichment_llm"]  = "FAILED"

    _queue_for_validation(enriched)
    return enriched


# --- Unknown product analysis ---

ANALYSIS_PROMPT = """
You are a clinical dietary data extractor and food scientist.
Analyse the following product label text and return a structured analysis.

GTIN: {gtin}

FRONT OF PACKAGE:
{text_front}

NUTRITION & INGREDIENTS PANEL:
{text_nutrition}

Extract:
1. Product name and brand from the front label
2. All ingredients as a clean list
3. Nutrition per 100g where visible
4. Clinical assessment: FODMAP, coeliac, histamine ratings, allergens, health summary

Return JSON only. No markdown.
"""

def analyse_product(
    gtin:           str,
    text_front:     str,
    text_nutrition: str,
) -> Optional[dict]:
    """
    Analyse an unknown product from OCR text.
    Returns an AnalysisResult-compatible dict or None on failure.
    """
    if not text_front and not text_nutrition:
        return None

    prompt = ANALYSIS_PROMPT.format(
        gtin           = gtin,
        text_front     = text_front or "(none)",
        text_nutrition = text_nutrition or "(none)",
    )

    # Build a synthetic record to pass through enrichment
    record = {
        "gtin":            gtin,
        "name":            "Unknown Product",
        "raw_ingredients": text_nutrition,
        "nutrition_100g":  None,
        "_enrichment_llm": None,
    }

    profile_data = _call_gemini({"raw_ingredients": text_nutrition, "name": text_front[:80]})

    if not profile_data:
        return None

    # Return in AnalysisResult shape expected by iOS
    return {
        "type":  "product",
        "items": [{
            "name":                text_front[:80] if text_front else gtin,
            "safe":                profile_data.get("fodmap_rating", -1) <= 1,
            "verdict":             _verdict(profile_data),
            "summary":             profile_data.get("health_summary", ""),
            "warnings":            profile_data.get("allergen_warnings", []),
            "ingredients":         [i.strip() for i in text_nutrition.split(",") if i.strip()][:20],
            "flaggedIngredients":  profile_data.get("allergen_warnings", []),
            "detailedReason":      profile_data.get("health_summary", ""),
        }]
    }


def _verdict(profile: dict) -> str:
    ratings = [
        profile.get("fodmap_rating", -1),
        profile.get("coeliac_rating", -1),
        profile.get("histamine_rating", -1),
    ]
    max_r = max(r for r in ratings if r >= 0) if any(r >= 0 for r in ratings) else -1
    if max_r >= 3:   return "Avoid"
    if max_r >= 2:   return "Caution"
    if max_r >= 1:   return "Low Risk"
    if max_r == 0:   return "Safe"
    return "Unknown"


# --- Main product lookup ---

def get_product(scanned_gtin: str) -> Optional[dict]:
    """
    Look up a product. Returns the record or None.

    Case 1: Enriched record  → return immediately
    Case 2: Unenriched record → return record (caller streams enrichment separately)
    Case 3: Not found        → return None
    """
    for candidate in _variations(scanned_gtin):
        record = _off.get(candidate)
        if record:
            return record

    return None


def is_enriched(record: dict) -> bool:
    """True if this record has already been through LLM enrichment."""
    return bool(record.get("_enrichment_llm"))
