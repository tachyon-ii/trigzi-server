#!/usr/bin/env python3
from __future__ import annotations
#
#  core/llm/skills.py
#  trigzi-backend
#
#  Prompt loading and variable injection. 
#  All prompt logic is decoupled into prompts/*.txt flat files.
#

import os
import json

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'prompts')

class SkillsLibrary:

    @staticmethod
    def _load_prompt(filename: str) -> str:
        path = os.path.join(_PROMPTS_DIR, filename)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def analyze_text_prompt(ocr_text: str, profile: str) -> str:
        template = SkillsLibrary._load_prompt('analyze_text.txt')
        return template.format(profile=profile, ocr_text=ocr_text)

    @staticmethod
    def analyze_food_image_prompt(profile: str) -> str:
        template = SkillsLibrary._load_prompt('analyze_food_image.txt')
        return template.format(profile=profile)

    @staticmethod
    def enrich_product_prompt(record: dict) -> str:
        template = SkillsLibrary._load_prompt('enrich_product.txt')
        return template.format(
            name=record.get('name', ''),
            raw_ingredients=record.get('raw_ingredients', ''),
            nutrition_100g=json.dumps(record.get('nutrition_100g', {})),
            health_star_rating=record.get('health_star_rating', 'None')
        )

    @staticmethod
    def analyse_menu_prompt(ocr_text: str) -> str:
        template = SkillsLibrary._load_prompt('analyse_menu.txt')
        return template.format(menu_text=ocr_text)
