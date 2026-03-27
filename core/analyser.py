#!/usr/bin/env python3
from __future__ import annotations
#
#  core/analyser.py
#
#  LLM analysis endpoints — fully async, no _run() bridge.
#  All three functions await the router directly.
#

import re
from typing import Optional

from core.llm.router import router
from core.llm.skills import SkillsLibrary

DEFAULT_MODELS = ["gemini", "claude", "openai"]


async def analyse_product(
    gtin:           str,
    text_front:     str,
    text_nutrition: str,
) -> Optional[dict]:
    """Analyse OCR text from an unknown product label."""
    if not text_front and not text_nutrition:
        return None

    combined = f"Front label:\n{text_front}\n\nNutrition/Ingredients:\n{text_nutrition}"

    try:
        response = await router.analyze(
            payload       = {"text": combined},
            profile       = "",
            model_strings = DEFAULT_MODELS,
            optimize      = "accuracy",
            timeout       = 60.0,
        )
        return response.get("result")
    except Exception as e:
        print(f"  [!] analyse_product failed: {e}")
        return None


async def analyse_meal(image: str, profile: str) -> Optional[dict]:
    """Analyse a base64-encoded JPEG meal photo."""
    if not image:
        return None

    try:
        response = await router.analyze(
            payload       = {"image_base64": image},
            profile       = profile,
            model_strings = DEFAULT_MODELS,
            optimize      = "accuracy",
            timeout       = 60.0,
        )
        return response.get("result")
    except Exception as e:
        print(f"  [!] analyse_meal failed: {e}")
        return None


async def analyse_menu(text: str, profile: str) -> Optional[dict]:
    """Analyse OCR-extracted menu text."""
    if not text:
        return None

    try:
        response = await router.analyze(
            payload       = {"text": text},
            profile       = profile,
            model_strings = DEFAULT_MODELS,
            optimize      = "accuracy",
            timeout       = 60.0,
        )
        return response.get("result")
    except Exception as e:
        print(f"  [!] analyse_menu failed: {e}")
        return None
