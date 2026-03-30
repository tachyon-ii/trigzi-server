#!/usr/bin/env python3
from __future__ import annotations
#
#  core/analyser.py
#

from typing import Optional
import json

from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.config import config as llm_config


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
        response = await router.analyze(
            payload       = {"text": combined},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        
        # Router now returns a raw string. Parse JSON here.
        raw_result = response.get("result")
        if not raw_result:
            return None
        return json.loads(raw_result)

    except json.JSONDecodeError as e:
        print(f"  [!] analyse_product JSON decode failed: {e}")
        return None
    except Exception as e:
        print(f"  [!] analyse_product failed: {e}")
        return None


async def analyse_meal(image: str, profile: str) -> Optional[dict]:
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
        
        # Router now returns a raw string. Parse JSON here.
        raw_result = response.get("result")
        if not raw_result:
            return None
        return json.loads(raw_result)

    except json.JSONDecodeError as e:
        print(f"  [!] analyse_meal JSON decode failed: {e}")
        return None
    except Exception as e:
        print(f"  [!] analyse_meal failed: {e}")
        return None


async def analyse_menu(text: str) -> Optional[dict]:
    """Analyse OCR-extracted menu text via rapid flat-text extraction."""
    if not text:
        return None

    _cfg = llm_config.task_config("analyse_menu")

    try:
        response = await router.analyze(
            payload       = {"menu_text": text},
            profile       = "", # No profile needed for extraction
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        
        raw_text = response.get("result", "")
        
        # Failsafe if the model somehow wrapped it in JSON despite instructions
        if isinstance(raw_text, dict):
            raw_text = json.dumps(raw_text)
        else:
            raw_text = str(raw_text)

        # The Dumb Parser (Optimized: No Quote)
        dishes = []
        blocks = [b.strip() for b in raw_text.split("---") if b.strip()]
        
        for block in blocks:
            dish_data = {
                "name": "Unknown",
                "listed_ingredients": [],
                "suspected_ingredients": []
            }
            
            for line in block.split('\n'):
                line = line.strip()
                if not line or ':' not in line: 
                    continue
                 
                key, val = [part.strip() for part in line.split(':', 1)]
                key = key.lower()
                
                if key == "dish": 
                    dish_data["name"] = val
                elif key == "listed": 
                    dish_data["listed_ingredients"] = [i.strip() for i in val.split(',') if i.strip()]
                elif key == "suspected": 
                    dish_data["suspected_ingredients"] = [i.strip() for i in val.split(',') if i.strip()]

            if dish_data["name"] != "Unknown":
                dishes.append(dish_data)

        return {"type": "menu", "items": dishes}

    except Exception as e:
        print(f"  [!] analyse_menu failed: {e}")
        return None
