#!/usr/bin/env python3
from __future__ import annotations
#
#  core/analyser.py
#

import re
import json
import logging
from typing import Optional

from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.config import config as llm_config
from core.llm.validator import SchemaValidator
from core.personality import get_persona_instruction

# Initialize the logger for this module
logger = logging.getLogger(__name__)


async def analyse_product(
    gtin:           str,
    text_front:     str,
    text_nutrition: str,
) -> Optional[dict]:
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
        
        raw_result = response.get("result")
        if not raw_result:
            return None
        return json.loads(raw_result)

    except json.JSONDecodeError as e:
        logger.error(f" analyse_product JSON decode failed: {e}")
        return None
    except Exception as e:
        logger.error(f" analyse_product failed: {e}")
        return None


async def analyse_meal(image: str, profile: str) -> Optional[dict]:
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
        
        raw_result = response.get("result")
        if not raw_result:
            return None
        return json.loads(raw_result)

    except json.JSONDecodeError as e:
        logger.error(f" analyse_meal JSON decode failed: {e}")
        return None
    except Exception as e:
        logger.error(f" analyse_meal failed: {e}")
        return None


async def analyse_menu(text: str) -> Optional[dict]:
    """Analyse OCR-extracted menu text via rapid flat-text extraction."""
    if not text:
        return None

    _cfg = llm_config.task_config("analyse_menu")

    try:
        response = await router.analyse(
            payload       = {"menu_text": text},
            profile       = "", # No profile needed for extraction
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        
        raw_text = response.get("result", "")
        
        if isinstance(raw_text, dict):
            raw_text = json.dumps(raw_text)
        else:
            raw_text = str(raw_text)

        # The Robust Parser (Leveraging multi-line regex validation)
        expected_keys = ["dish", "listed", "suspected"]
        extracted_blocks = SchemaValidator.extract_blocks(raw_text, expected_keys)
        
        dishes = []
        for block in extracted_blocks:
            dish_data = {
                "name": block.get("dish", "Unknown"),
                "listed_ingredients": [i.strip() for i in block.get("listed", "").split(',') if i.strip()],
                "suspected_ingredients": [i.strip() for i in block.get("suspected", "").split(',') if i.strip()]
            }
            if dish_data["name"] != "Unknown":
                dishes.append(dish_data)

        return {"type": "menu", "items": dishes}

    except Exception as e:
        logger.error(f" analyse_menu failed: {e}")
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
            "energy_kj", "calories_kcal", "protein_g", "fat_total_g", 
            "fat_saturated_g", "carbohydrates_g", "sugars_g", "fibre_g", "sodium_mg"
        ]
        
        blocks = SchemaValidator.extract_blocks(raw_text, expected_keys)
        parsed = blocks[0] if blocks else {}

        final_nutrition = {}
        for key in expected_keys:
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
        logger.error(f" enrich_nutrition failed: {e}", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Chat Pipeline (Pipelined for SSE Streaming)
# ---------------------------------------------------------------------------

async def chat_assistant(system_context: dict, history: list, message: str, trigzi_nickname: str = "Trigzi") -> tuple[Optional[str], Optional[str]]:
    """Task 1: Generate the clinical response text and intercept UI actions. Returns (text, action)"""
    if not message:
        return None, None

    # Defensive type coercion
    if isinstance(system_context, str):
        try:
            system_context = json.loads(system_context)
        except Exception:
            system_context = {"dietary_profile": system_context}
    elif not isinstance(system_context, dict):
        system_context = {}

    persona_instruction = get_persona_instruction(system_context)

    # Context Aggregation: Dietary Profile + Spatial/Temporal Environment
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

    # INJECT into prompt builder:
    prompt = SkillsLibrary.chat_assistant_prompt(
        system_context=ctx_str,
        history=hist_str,
        message=message,
        trigzi_nickname=trigzi_nickname,
        persona_instruction=persona_instruction
    )

    # DYNAMIC CONFIG ROUTING
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

        # =================================================================
        # THE ACTION INTERCEPTOR (UI OVERRIDES)
        # =================================================================
        action_command = None
        action_match = re.search(r'\[ACTION:\s*([^\]]+)\]', raw_text)
        
        if action_match:
            action_command = action_match.group(1).strip()
            # Strip the tag from the text so the user never sees it
            raw_text = re.sub(r'\[ACTION:\s*[^\]]+\]', '', raw_text).strip()

        return raw_text, action_command

    except Exception as e:
        logger.error(f" chat_assistant failed: {e}", exc_info=True)
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
        logger.error(f" chat_emoji failed: {e}")
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
            if not line or line == "---": continue

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
        logger.error(f" onboarding_assistant failed: {e}")
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
            optimize      = "accuracy", # FORCE high-end model for EQ and safety
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
        logger.error(f" sigmund_assistant failed: {e}")
        return None, None
