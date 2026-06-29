#!/usr/bin/env python3
"""
=============================================================================
Module:        LLM Product Enricher
Location:      core/enricher.py
Description:   LLM enrichment pipeline for raw product records.
               Intercepts unenriched database records, requests clinical
               and dietary annotations from the LLM routing layer, and
               deterministically parses raw ingredient strings into
               tokenized arrays for the client-side safety tripwires.

Architecture Note:
This module is fully asynchronous and handles DB writes for the
enrichment pipeline. It updates the `products` table with the
generated `clinical_profile` and writes the exact prompt/model pairing
back to the `enrichments` table as a foreign key (`enrichment_id`).
=============================================================================
"""

from __future__ import annotations

import os
import json
import asyncio
from typing import Optional

from utils.off_lookup import OFFLookup
from utils.ingredient_parser import parse_ingredients
from core.db import get_or_create_enrichment
from core.telemetry import log_scan
from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.config import config as llm_config

# Path to the JSONL log file for human validation of LLM outputs
VALIDATE_JSONL = "/var/www/trigzi/logs/validate.jsonl"

# Current schema version for the enrichment prompt
PROMPT_VER     = "enrich_v1"

# Categories that do not require clinical gut health profiles
NON_FOOD_CATEGORIES = {
    "Cleaning & Laundry",
    "Home & Garden",
    "Health & Beauty",
    "Pet",
    "Tobacco",
}

# Keys emitted by prompts/enrich_product.txt -> [OUTPUT] block.
# Lower-cased and with spaces->underscores to match SchemaValidator output.
_PROMPT_KEYS_RAW = [
    "estimated health star",
    "fodmap rating",
    "coeliac rating",
    "histamine rating",
    "allergens",
    "health summary",
]

# Mapping dictionary to translate raw prompt keys into the canonical database schema
_KEY_RENAME = {
    "estimated health star": "estimated_health_star",
    "fodmap rating":         "fodmap_rating",
    "coeliac rating":        "coeliac_rating",
    "histamine rating":      "histamine_rating",
    "allergens":             "allergen_warnings",
    "health summary":        "health_summary",
}

# Instantiate the singleton database lookup utility
_off = OFFLookup()


def _nop_profile() -> dict:
    """
    Generate a baseline 'No Operation' clinical profile for non-food items.
    
    Returns:
        dict: A neutral clinical profile bypassing dietary threat checks.
    """
    return {
        "estimated_health_star": None,
        "fodmap_rating":         -1,
        "coeliac_rating":        -1,
        "histamine_rating":      -1,
        "allergen_warnings":     [],
        "health_summary":        "Non-food item. No clinical gut health profile applies."
    }


def _coerce_clinical(block: dict) -> dict:
    """
    Normalise a raw flat-text block into the canonical clinical_profile dict.

    Operations performed:
    - Renames space-separated keys to snake_case (e.g. "fodmap rating" -> "fodmap_rating")
    - Coerces numeric ratings to int (defaults to -1 on parse failure)
    - Coerces estimated health star to float or None
    - Splits the comma-separated allergens string into a sanitized list

    Args:
        block (dict): The raw parsed block from the LLM response.

    Returns:
        dict: The strictly typed and formatted clinical profile.
    """
    out: dict = {}
    for raw_key in _PROMPT_KEYS_RAW:
        target = _KEY_RENAME[raw_key]
        value  = block.get(raw_key, "")

        if target == "estimated_health_star":
            try:
                out[target] = float(value) if value not in ("", "null", "None") else None
            except (TypeError, ValueError):
                out[target] = None

        elif target in ("fodmap_rating", "coeliac_rating", "histamine_rating"):
            try:
                out[target] = int(float(value))
            except (TypeError, ValueError):
                out[target] = -1

        elif target == "allergen_warnings":
            if isinstance(value, list):
                out[target] = [str(v).strip() for v in value if str(v).strip()]
            else:
                out[target] = [t.strip() for t in str(value).split(",") if t.strip()]

        else:  # health_summary
            out[target] = str(value).strip() or ""

    return out


def _queue_for_validation_sync(record: dict) -> None:
    """
    Synchronously append an enriched record to the validation JSONL log.
    Catches and suppresses I/O errors to prevent halting the enrichment pipeline.

    Args:
        record (dict): The fully enriched product record.
    """
    try:
        os.makedirs(os.path.dirname(VALIDATE_JSONL), exist_ok=True)
        with open(VALIDATE_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f"  [!] validate queue write failed: {e}")


def _queue_for_validation(record: dict) -> None:
    """
    Asynchronously offload the validation logging to a background thread.
    
    Args:
        record (dict): The fully enriched product record.
    """
    asyncio.create_task(asyncio.to_thread(_queue_for_validation_sync, record))


async def enrich(record: dict) -> dict:
    """
    Enrich a raw product record with a clinical profile and parsed ingredients.

    This function intercepts the record, deterministically parses the raw ingredients 
    using the native engine, and requests a clinical profile from the LLM router.
    It writes the `enrichment_id` foreign key back to the database to preserve lineage.

    Args:
        record (dict): The unenriched product dictionary.

    Returns:
        dict: The mutated product dictionary containing the `clinical_profile`, 
              `parsed_ingredients`, and `_enrichment_llm` tag.
    """
    enriched     = dict(record)
    llm_model    = "NOP"
    profile_data: Optional[dict] = None
    prompt_text  = ""

    # 1. Deterministic Tokenization (Bypassing the LLM)
    raw_ing = enriched.get("raw_ingredients", "")
    enriched["parsed_ingredients"] = parse_ingredients(raw_ing) if raw_ing else []

    # 2. Category Gating
    if record.get("category") in NON_FOOD_CATEGORIES:
        profile_data = _nop_profile()
    else:
        # 3. LLM Orchestration
        log_scan(
            gtin   = record.get("gtin", ""),
            source = record.get("_source_name", "off"),
            text   = record.get("raw_ingredients", "")
        )

        prompt_text = SkillsLibrary.enrich_product_prompt(record)
        _cfg = llm_config.task_config("enrich")

        try:
            response = await router.analyse(
                payload       = {"product": record, "prompt": prompt_text},
                profile       = "",
                model_strings = _cfg["models"],
                optimize      = _cfg["optimize"],
                timeout       = _cfg["timeout"],
                expected_keys = _PROMPT_KEYS_RAW,
            )

            llm_model = response.get("model", "router")

            blocks = response.get("parsed_blocks") or []
            if blocks:
                profile_data = _coerce_clinical(blocks[0])
            else:
                # Fallback defense if extraction fails despite upstream catching
                print("  [!] Enrichment: no parsed blocks in router response.")

        except Exception as e:
            print(f"  [!] Enrichment failed: {e}")
            llm_model = "FAILED"

    # 4. Database Persistence
    if profile_data:
        enriched["clinical_profile"] = profile_data
        enriched["_enrichment_llm"]  = llm_model

        enrichment_id = await get_or_create_enrichment(
            task        = "product",
            llm_model   = llm_model,
            prompt_ver  = PROMPT_VER,
            prompt_text = prompt_text or "NOP",
        )

        _queue_for_validation(enriched)
        await _off.save(enriched, enrichment_id=enrichment_id)
    else:
        enriched["_enrichment_llm"] = "FAILED"
        await _off.save(enriched, enrichment_id=None)

    return enriched


async def patch_nutrition(gtin: str, nutrition_data: dict) -> bool:
    """
    Update a product's nutrition data directly in the database.

    Called primarily when the frontend pushes missing OCR panel data back 
    to the server for an existing product.

    Args:
        gtin (str): The product barcode.
        nutrition_data (dict): The extracted 100g/100ml nutrition macros.

    Returns:
        bool: True if the record was successfully found and patched, False otherwise.
    """
    record = await _off.get(gtin)
    if record:
        record["nutrition_100g"] = nutrition_data
        # _off.save safely handles preserving the existing enrichment_id
        await _off.save(record)
        return True
    return False
