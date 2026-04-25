#!/usr/bin/env python3
from __future__ import annotations
#
#  core/enricher.py
#
#  LLM enrichment pipeline for raw product records.
#  Fully async — no _run() bridge needed.
#
#  Writes enrichment_id FK back to products so the exact prompt×model
#  that produced each clinical profile is permanently recorded.
#

import os
import json
import asyncio
from typing import Optional

from utils.off_lookup import OFFLookup
from core.db import get_or_create_enrichment
from core.telemetry import log_scan
from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.config import config as llm_config

VALIDATE_JSONL = "/var/www/trigzi/logs/validate.jsonl"
PROMPT_VER     = "enrich_v1"

NON_FOOD_CATEGORIES = {
    "Cleaning & Laundry",
    "Home & Garden",
    "Health & Beauty",
    "Pet",
    "Tobacco",
}

# Keys emitted by prompts/enrich_product.txt -> [OUTPUT] block,
# lower-cased and with spaces->underscores to match SchemaValidator output.
# Source headers in the prompt are: "Estimated Health Star", "FODMAP Rating",
# "Coeliac Rating", "Histamine Rating", "Allergens", "Health Summary".
# extract_blocks lowercases and preserves spaces, so we accept both forms
# and normalise on the way out.
_PROMPT_KEYS_RAW = [
    "estimated health star",
    "fodmap rating",
    "coeliac rating",
    "histamine rating",
    "allergens",
    "health summary",
]

_KEY_RENAME = {
    "estimated health star": "estimated_health_star",
    "fodmap rating":         "fodmap_rating",
    "coeliac rating":        "coeliac_rating",
    "histamine rating":      "histamine_rating",
    "allergens":             "allergen_warnings",
    "health summary":        "health_summary",
}

_off = OFFLookup()


def _nop_profile() -> dict:
    return {
        "estimated_health_star": None,
        "fodmap_rating":         -1,
        "coeliac_rating":        -1,
        "histamine_rating":      -1,
        "allergen_warnings":     [],
        "health_summary":        "Non-food item. No clinical gut health profile applies."
    }


def _coerce_clinical(block: dict) -> dict:
    """Normalise a raw flat-text block into the canonical clinical_profile dict.

    - Renames space-keys to snake_case (e.g. "fodmap rating" -> "fodmap_rating")
    - Coerces numeric ratings to int (-1 on parse failure)
    - Coerces estimated health star to float or None
    - Splits the comma-separated allergens string into a list
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
    try:
        os.makedirs(os.path.dirname(VALIDATE_JSONL), exist_ok=True)
        with open(VALIDATE_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f"  [!] validate queue write failed: {e}")


def _queue_for_validation(record: dict) -> None:
    asyncio.create_task(asyncio.to_thread(_queue_for_validation_sync, record))


async def enrich(record: dict) -> dict:
    """Enrich a raw product record with a clinical profile.

    Writes enrichment_id FK back to the products table.
    """
    enriched     = dict(record)
    llm_model    = "NOP"
    profile_data: Optional[dict] = None
    prompt_text  = ""

    if record.get("category") in NON_FOOD_CATEGORIES:
        profile_data = _nop_profile()
    else:
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
                expected_keys = _PROMPT_KEYS_RAW,           # <-- NEW
            )

            llm_model = response.get("model", "router")

            blocks = response.get("parsed_blocks") or []
            if blocks:
                profile_data = _coerce_clinical(blocks[0])
            else:
                # Should not happen — BaseProvider raises decode_failed when
                # expected_keys are set and no blocks are extracted — but be
                # defensive in case the contract is ever loosened.
                print("  [!] Enrichment: no parsed blocks in router response.")

        except Exception as e:
            print(f"  [!] Enrichment failed: {e}")
            llm_model = "FAILED"

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
    """Update a product's nutrition data directly in the database."""
    record = await _off.get(gtin)
    if record:
        record["nutrition_100g"] = nutrition_data
        # _off.save handles preserving the existing enrichment_id
        await _off.save(record)
        return True
    return False
