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
    """
    Enrich a raw product record with a clinical profile.
    Writes enrichment_id FK back to the products table.
    """
    enriched     = dict(record)
    llm_model    = "NOP"
    profile_data = None
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
            )
        
            profile_data = response.get("result")
            llm_model    = response.get("model", "router")
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
        # lookup.save handles preserving the existing enrichment_id
        await _off.save(record)
        return True
    return False
