#!/usr/bin/env python3
from __future__ import annotations
#
#  core/analyser.py
#
#  LLM analysis endpoints — fully async, no _run() bridge.
#  All three functions await the router directly.
#
#  Routing (models, optimize, timeout) is read per-task from
#  llm_providers.json via llm_config.task_config().
#  No routing constants live in this file.
#

from typing import Optional

from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.config import config as llm_config


async def analyse_product(
    gtin:           str,
    text_front:     str,
    text_nutrition: str,
) -> Optional[dict]:
    """Analyse OCR text from an unknown product label."""
    if not text_front and not text_nutrition:
        return None

    combined = f"Front label:\n{text_front}\n\nNutrition/Ingredients:\n{text_nutrition}"
    _cfg = llm_config.task_config("analyse_product")

    try:
        response = await router.analyze(
            payload       = {"text": combined},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        return response.get("result")
    except Exception as e:
        print(f"  [!] analyse_product failed: {e}")
        return None


async def analyse_meal(image: str, profile: str) -> Optional[dict]:
    """Analyse a base64-encoded JPEG meal photo."""
    if not image:
        return None

    _cfg = llm_config.task_config("analyse_meal")

    try:
        response = await router.analyze(
            payload       = {"image_base64": image},
            profile       = profile,
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        return response.get("result")
    except Exception as e:
        print(f"  [!] analyse_meal failed: {e}")
        return None


async def analyse_menu(text: str, profile: str) -> Optional[dict]:
    """Analyse OCR-extracted menu text."""
    if not text:
        return None

    _cfg = llm_config.task_config("analyse_menu")

    try:
        response = await router.analyze(
            payload       = {"text": text},
            profile       = profile,
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        return response.get("result")
    except Exception as e:
        print(f"  [!] analyse_menu failed: {e}")
        return None
