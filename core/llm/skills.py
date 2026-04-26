#!/usr/bin/env python3
"""
=============================================================================
Module:        Skills Library
Location:      core/llm/skills.py
Description:   Prompt loading and variable injection. All prompt logic is
               decoupled into prompts/*.txt flat files; this class is the
               single point that reads them off disk and substitutes
               per-request variables.

Architecture Note:
Each public method maps 1:1 to a prompt template file. Adding a new
LLM task means dropping a new template into prompts/ and adding a
single ``@staticmethod`` here that loads it and forwards variables to
``str.format``. Prompts are validated downstream by
SchemaValidator.validate_prompt_contract before being sent to any
provider.
=============================================================================
"""

from __future__ import annotations

import os
import json

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prompts')

class SkillsLibrary:
    """Static utility class for loading and rendering prompt templates.

    Each public method corresponds to one ``prompts/*.txt`` file and one
    LLM task. The class itself holds no state — every method is a
    ``@staticmethod`` that reads its template fresh from disk and
    interpolates variables via ``str.format``.

    Templates live in ``prompts/`` at the repo root, resolved relative to
    this file's location at import time (see ``_PROMPTS_DIR``).
    """

    @staticmethod
    def _load_prompt(filename: str) -> str:
        """Read a prompt template from the prompts/ directory and return its raw text."""
        path = os.path.join(_PROMPTS_DIR, filename)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def analyse_text_prompt(ocr_text: str, profile: str) -> str:
        """Build the prompt for analysing OCR-extracted product label text against a dietary profile."""
        template = SkillsLibrary._load_prompt('analyse_text.txt')
        return template.format(profile=profile, ocr_text=ocr_text)

    @staticmethod
    def analyse_food_image_prompt(profile: str) -> str:
        """Build the prompt for analysing a food photo against a dietary profile."""
        template = SkillsLibrary._load_prompt('analyse_food_image.txt')
        return template.format(profile=profile)

    @staticmethod
    def enrich_product_prompt(record: dict) -> str:
        """Build the prompt for enriching a sparse Open Food Facts product record."""
        template = SkillsLibrary._load_prompt('enrich_product.txt')
        return template.format(
            name=record.get('name', ''),
            raw_ingredients=record.get('raw_ingredients', ''),
            nutrition_100g=json.dumps(record.get('nutrition_100g', {})),
            health_star_rating=record.get('health_star_rating', 'None')
        )

    @staticmethod
    def analyse_menu_prompt(ocr_text: str) -> str:
        """Build the prompt for extracting dishes and ingredients from OCR-scanned menu text."""
        template = SkillsLibrary._load_prompt('analyse_menu.txt')
        return template.format(menu_text=ocr_text)

    @staticmethod
    def chat_assistant_prompt(
        system_context:      str,
        history:             str,
        message:             str,
        trigzi_nickname:     str = "Trigzi",
        persona_instruction: str = "",
    ) -> str:
        """Build the main clinical chat-assistant prompt with persona and conversation history."""
        template = SkillsLibrary._load_prompt('chat_assistant.txt')
        return template.format(
            system_context=system_context,
            history=history,
            message=message,
            trigzi_nickname=trigzi_nickname,
            persona_instruction=persona_instruction # <-- MUST BE HERE
        )

    @staticmethod
    def chat_emoji_prompt(text: str) -> str:
        """Build the micro-inference prompt for selecting an emoji to append to a chat reply."""
        template = SkillsLibrary._load_prompt('chat_emoji.txt')
        return template.format(text=text)

    @staticmethod
    def onboarding_prompt(message: str, fallback_name: str, trigzi_nickname: str = "Trigzi") -> str:
        """Build the scripted-onboarding prompt that drives first-run user setup."""
        template = SkillsLibrary._load_prompt('onboarding.txt')
        return template.format(
            message=message,
            fallback_name=fallback_name,
            trigzi_nickname=trigzi_nickname
        )

    @staticmethod
    def sigmund_assistant_prompt(system_context: str, history: str, message: str) -> str:
        """Build the high-EQ Sigmund-intercept prompt for psychological de-escalation."""
        template = SkillsLibrary._load_prompt('sigmund_assistant.txt')
        return template.format(
            system_context=system_context,
            history=history,
            message=message
        )

    @staticmethod
    def enrich_nutrition_prompt(ocr_text: str) -> str:
        """Build the prompt for extracting structured nutrition data from an OCR-scanned panel."""
        template = SkillsLibrary._load_prompt('enrich_nutrition.txt')
        return template.format(ocr_text=ocr_text)
