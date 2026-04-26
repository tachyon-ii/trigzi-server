#!/usr/bin/env python3
"""
=============================================================================
Module:        LLM Analyser
Location:      core/analyser.py
Description:   Business logic layer for LLM interactions.
               Orchestrates task-specific prompt construction, context
               aggregation, and strict schema validation of LLM outputs.

Architecture Note:
This layer receives raw strings from the LLM Router and
processes them via the SchemaValidator. It enforces the
backend's 'Death to JSON' flat-text data extraction policy
before yielding parsed Python dictionaries back to the
transport layer (app.py).
=============================================================================
"""

# pylint: disable=duplicate-code
# Justification: shares the chat-history formatting + prompt-build
# pattern with scripts/benchmark_task.py. Both files build a
# SkillsLibrary chat/sigmund prompt by joining history messages with
# role labels — the benchmark harness intentionally mirrors the
# production code path so its results reflect what the analyser
# actually sends. Refactoring would couple the benchmark to internal
# helpers and reduce its value as an independent reference.

from __future__ import annotations

import re
import json
import logging
from typing import Optional

from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.config import config as llm_config
from core.llm.validator import SchemaValidator
from core.personality import get_persona_instruction

logger = logging.getLogger(__name__)


async def analyse_product(
    gtin:           str,
    text_front:     str,
    text_nutrition: str,
) -> Optional[dict]:
    """Analyse OCR-extracted product label/nutrition text via flat-text LLM extraction."""
    if not text_front and not text_nutrition:
        return None

    combined = f"Front label:\n{text_front}\n\nNutrition/Ingredients:\n{text_nutrition}"
    _cfg = llm_config.task_config("analyse_product")

    try:
        response = await router.analyse(
            payload       = {"text": combined},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )

        raw_text = str(response.get("result", ""))

        expected_keys = [
            "valid_input", "item", "verdict", "summary", "warnings", "ingredients", "flagged", "reasoning"
        ]

        blocks = SchemaValidator.extract_blocks(raw_text, expected_keys)
        if not blocks:
            return None

        # The LLM Semantic Validation Gate
        if str(blocks[0].get("valid_input", "")).lower() == "false":
            logger.warning("LLM flagged product input data as invalid/irrelevant.")
            return None

        # Clean the block before returning to the UI layer
        blocks[0].pop("valid_input", None)
        return blocks[0]

    except Exception as e:
        logger.error("analyse_product failed: %s for gtin %s", e, gtin)
        return None


async def analyse_meal(image: str, profile: str) -> Optional[dict]:
    """Analyse a base64-encoded meal photo against a dietary profile via the LLM."""
    if not image:
        return None

    _cfg = llm_config.task_config("analyse_meal")

    try:
        response = await router.analyse(
            payload       = {"image_base64": image},
            profile       = profile,
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )

        raw_text = str(response.get("result", ""))

        expected_keys = [
            "valid_input", "dish", "verdict", "summary", "warnings", "ingredients", "flagged", "reasoning"
        ]

        blocks = SchemaValidator.extract_blocks(raw_text, expected_keys)
        if not blocks:
            return None

        # The LLM Semantic Validation Gate
        if str(blocks[0].get("valid_input", "")).lower() == "false":
            logger.warning("LLM flagged meal image data as invalid/irrelevant.")
            return None

        blocks[0].pop("valid_input", None)
        return blocks[0]

    except Exception as e:
        logger.error("analyse_meal failed: %s", e)
        return None


async def analyse_menu(text: str) -> Optional[dict]:
    """Analyse OCR-extracted menu text via rapid flat-text extraction."""
    if not text:
        return None

    _cfg = llm_config.task_config("analyse_menu")

    try:
        response = await router.analyse(
            payload       = {"menu_text": text},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )

        raw_text = response.get("result", "")

        if isinstance(raw_text, dict):
            raw_text = json.dumps(raw_text)
        else:
            raw_text = str(raw_text)

        expected_keys = ["valid_input", "dish", "listed", "suspected"]
        extracted_blocks = SchemaValidator.extract_blocks(raw_text, expected_keys)

        if not extracted_blocks:
            return None

        # The LLM Semantic Validation Gate
        if str(extracted_blocks[0].get("valid_input", "")).lower() == "false":
            logger.warning("LLM flagged menu input data as invalid/irrelevant.")
            return None

        dishes = []
        for block in extracted_blocks:
            dish_data = {
                "name": block.get("dish", "Unknown"),
                "listed_ingredients": [i.strip() for i in block.get("listed", "").split(',') if i.strip()],
                "suspected_ingredients": [i.strip() for i in block.get("suspected", "").split(',') if i.strip()]
            }
            if dish_data["name"] != "Unknown":
                dishes.append(dish_data)

        if not dishes:
            return None

        return {"type": "menu", "items": dishes}

    except Exception as e:
        logger.error("analyse_menu failed: %s", e)
        return None


async def enrich_nutrition(gtin: str, ocr_text: str) -> Optional[dict]:
    """Extract missing nutrition data from an OCR panel using flat-text parsing."""
    if not ocr_text:
        return None

    prompt = SkillsLibrary.enrich_nutrition_prompt(ocr_text)

    _cfg = llm_config.task_config("enrich_nutrition")

    try:
        response = await router.analyse(
            payload       = {"prompt": prompt},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )

        raw_text = str(response.get("result", "")).strip()

        expected_keys = [
            "valid_input", "energy_kj", "calories_kcal", "protein_g", "fat_total_g",
            "fat_saturated_g", "carbohydrates_g", "sugars_g", "fibre_g", "sodium_mg"
        ]

        blocks = SchemaValidator.extract_blocks(raw_text, expected_keys)
        if not blocks:
            return None

        # The LLM Semantic Validation Gate
        if str(blocks[0].get("valid_input", "")).lower() == "false":
            logger.warning("LLM flagged nutrition OCR data as invalid/irrelevant.")
            return None

        parsed = blocks[0]

        final_nutrition = {}
        for key in expected_keys:
            if key == "valid_input":
                continue

            val = parsed.get(key, "")
            if val:
                try:
                    final_nutrition[key] = float(val)
                except ValueError:
                    final_nutrition[key] = None
            else:
                final_nutrition[key] = None

        return final_nutrition

    except Exception as e:
        logger.error("enrich_nutrition failed: %s for gtin %s", e, gtin, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Chat Pipeline
# ---------------------------------------------------------------------------

async def chat_assistant(
    system_context:  dict,
    history:         list,
    message:         str,
    trigzi_nickname: str = "Trigzi",
) -> tuple[Optional[str], Optional[str]]:
    """Task 1: Generate the clinical response text and intercept UI actions. Returns (text, action)"""
    if not message:
        return None, None

    if isinstance(system_context, str):
        try:
            system_context = json.loads(system_context)
        except Exception:
            system_context = {"dietary_profile": system_context}
    elif not isinstance(system_context, dict):
        system_context = {}

    persona_instruction = get_persona_instruction(system_context)

    ctx_parts = [
        f"Dietary Profile: {json.dumps(system_context.get('dietary_profile', {}))}"
    ]
    if 'current_menu' in system_context:
        ctx_parts.append(f"Active Menu Context: {json.dumps(system_context['current_menu'])}")
    if 'spatial_context' in system_context:
        ctx_parts.append(f"Spatial Context: {system_context['spatial_context']}")
    if 'temporal_context' in system_context:
        ctx_parts.append(f"Recent User Focus:\n{system_context['temporal_context']}")

    ctx_str = "\n\n".join(ctx_parts)

    hist_str = ""
    for msg in history:
        role = "User" if msg.get("role") == "user" else "Trigzi"
        hist_str += f"{role}: {msg.get('content', '')}\n"

    prompt = SkillsLibrary.chat_assistant_prompt(
        system_context=ctx_str,
        history=hist_str,
        message=message,
        trigzi_nickname=trigzi_nickname,
        persona_instruction=persona_instruction
    )

    _cfg = llm_config.task_config("chat_assistant")

    try:
        response = await router.analyse(
            payload       = {"prompt": prompt},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg.get("optimize", "failover"),
            timeout       = _cfg.get("timeout", 12),
        )

        raw_text = str(response.get("result", "")).strip()

        if raw_text.lower().startswith("response:"):
            raw_text = raw_text[9:]
        raw_text = raw_text.strip(" \n-")

        action_command = None
        action_match = re.search(r'\[ACTION:\s*([^\]]+)\]', raw_text)

        if action_match:
            action_command = action_match.group(1).strip()
            if action_command.upper() == "NONE":
                action_command = None
            raw_text = re.sub(r'\[ACTION:\s*[^\]]+\]', '', raw_text).strip()

        return raw_text, action_command

    except Exception as e:
        logger.error("chat_assistant failed: %s", e, exc_info=True)
        return None, None


async def chat_emoji(text: str) -> str:
    """Task 2: Micro-inference to safely append an emoji."""
    if not text:
        return ""

    prompt = SkillsLibrary.chat_emoji_prompt(text=text)
    _cfg = llm_config.task_config("chat_emoji")

    try:
        response = await router.analyse(
            payload       = {"prompt": prompt},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg.get("optimize", "speed"),
            timeout       = _cfg.get("timeout", 3.0),
        )

        emoji_str = str(response.get("result", "")).strip()

        if not emoji_str or "none" in emoji_str.lower():
            return ""

        return emoji_str[0]

    except Exception as e:
        logger.error("chat_emoji failed: %s", e)
        return ""


async def onboarding_assistant(message: str, fallback_name: str, trigzi_nickname: str = "Trigzi") -> tuple[list[dict], str]:
    """Task: Execute scripted onboarding. Returns (events, text_to_emojify)."""
    if not message:
        return [{"event": "error", "data": {"message": "Empty message."}}], ""

    prompt = SkillsLibrary.onboarding_prompt(message, fallback_name, trigzi_nickname)
    _cfg = llm_config.task_config("chat_assistant")

    try:
        response = await router.analyse(
            payload       = {"prompt": prompt},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg.get("optimize", "failover"),
            timeout       = _cfg.get("timeout", 12),
        )

        raw_text = str(response.get("result", "")).strip()
        if raw_text.lower().startswith("response:"):
            raw_text = raw_text[9:]
        raw_text = raw_text.strip(" \n-")

        events = []
        text_content = ""

        for line in raw_text.split('\n'):
            line = line.strip()
            if not line or line == "---":
                continue

            if line.startswith("Message:"):
                text_content = line[8:].strip()
                events.append({"event": "text", "data": {"content": text_content}})

            elif line.startswith("Fact:"):
                k, v = line[5:].split("=", 1)
                events.append({"event": "fact", "data": {"key": k.strip(), "value": v.strip()}})

            elif line.startswith("Action:"):
                action_data = line[7:].strip()
                if "|" in action_data:
                    tool, param = action_data.split("|", 1)
                    events.append({"event": "action", "data": {"tool": tool.strip(), "param": param.strip()}})
                else:
                    events.append({"event": "action", "data": {"tool": action_data.strip(), "param": ""}})

        return events, text_content

    except Exception as e:
        logger.error("onboarding_assistant failed: %s", e)
        return [{"event": "error", "data": {"message": "Analysis failed."}}], ""


async def sigmund_assistant(system_context: dict, history: list, message: str) -> tuple[Optional[str], Optional[str]]:
    """Task 1b: The Sigmund Intercept. High-EQ psychological de-escalation."""
    if not message:
        return None, None

    if isinstance(system_context, str):
        try:
            system_context = json.loads(system_context)
        except Exception:
            system_context = {"dietary_profile": system_context}
    elif not isinstance(system_context, dict):
        system_context = {}

    ctx_str = (
        f"Dietary Profile: {json.dumps(system_context.get('dietary_profile', {}))}\n"
        f"Active Menu Context: {json.dumps(system_context.get('current_menu', []))}"
    )

    hist_str = ""
    for msg in history:
        role = "User" if msg.get("role") == "user" else "Trigzi"
        hist_str += f"{role}: {msg.get('content', '')}\n"

    prompt = SkillsLibrary.sigmund_assistant_prompt(
        system_context=ctx_str,
        history=hist_str,
        message=message
    )

    _cfg = llm_config.task_config("chat_sigmund")

    try:
        response = await router.analyse(
            payload       = {"prompt": prompt},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = "accuracy",
            timeout       = _cfg.get("timeout", 15),
        )

        raw_text = str(response.get("result", "")).strip()

        if raw_text.lower().startswith("response:"):
            raw_text = raw_text[9:]
        raw_text = raw_text.strip(" \n-")

        action_command = None
        action_match = re.search(r'\[ACTION:\s*([^\]]+)\]', raw_text)

        if action_match:
            action_command = action_match.group(1).strip()
            raw_text = re.sub(r'\[ACTION:\s*[^\]]+\]', '', raw_text).strip()

        return raw_text, action_command

    except Exception as e:
        logger.error("sigmund_assistant failed: %s", e)
        return None, None
