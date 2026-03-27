#!/usr/bin/env python3
from __future__ import annotations
#
#  scripts/llm_push.py
#
#  Stage 1: Send a scan file to the LLM router and write the raw response.
#
#  Routes through core/llm/router.py for provider failover.
#  Provider is selected by --provider (default: gemini).
#
#  The raw response is saved to logs/llm_responses/<timestamp>_<gtin>.txt
#  for processing by llm_pull.py (Stage 2).
#
#  Usage:
#      ./scripts/llm_push.py logs/scans/1743000000_9310077217814_ocr.txt
#      ./scripts/llm_push.py logs/scans/1743000000_9310077217814_ocr.txt --prompt prompts/extract_v2.txt
#      ./scripts/llm_push.py logs/scans/1743000000_9310077217814_ocr.txt --provider claude
#

import os
import sys
import time
import json
import asyncio
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.llm.router import router

RESPONSES_DIR  = os.path.join(os.path.dirname(__file__), '..', 'logs', 'llm_responses')
DEFAULT_PROMPT = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'extract_v1.txt')


def load_scan(path: str) -> dict:
    """Parse a scan file into its component parts."""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    parts = {}
    for line in content.split('\n'):
        for key in ('GTIN', 'TIMESTAMP', 'SOURCE'):
            if line.startswith(f'{key}:'):
                parts[key.lower()] = line.split(':', 1)[1].strip()

    for section in ('FRONT OF PACKAGE', 'NUTRITION & INGREDIENTS', 'INGREDIENTS'):
        marker = f'=== {section} ==='
        if marker in content:
            after = content.split(marker, 1)[1]
            text  = after.split('===', 1)[0] if '===' in after else after
            parts[section.lower().replace(' ', '_').replace('&_', '')] = text.strip()

    return parts


def load_prompt(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


async def call_router(prompt: str, provider: str, timeout: float) -> tuple[str, str]:
    """Call the LLM router. Returns (response_text, model_used)."""
    response = await router.analyze(
        payload       = {"prompt": prompt},
        profile       = "",
        model_strings = [provider],
        optimize      = "accuracy",
        timeout       = timeout,
    )
    result = response.get("result", {})
    model  = response.get("model", provider)

    # result may be a dict (JSON) or a raw string depending on provider
    if isinstance(result, dict):
        text = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        text = str(result)

    return text, model


def run(scan_path: str, prompt_path: str, provider: str, timeout: float) -> str:
    print(f"Scan     : {scan_path}")
    print(f"Prompt   : {prompt_path}")
    print(f"Provider : {provider}")

    scan            = load_scan(scan_path)
    prompt_template = load_prompt(prompt_path)

    prompt = prompt_template.format(
        text_front     = scan.get('front_of_package', ''),
        text_nutrition = scan.get('nutrition_ingredients', '') or scan.get('ingredients', ''),
    )

    print(f"Calling router ({provider})...")
    t0               = time.time()
    text, model_used = asyncio.run(call_router(prompt, provider, timeout))
    elapsed          = time.time() - t0
    print(f"Response in {elapsed:.1f}s ({len(text)} chars) via {model_used}")

    os.makedirs(RESPONSES_DIR, exist_ok=True)
    gtin     = scan.get('gtin', 'unknown')
    out_file = os.path.join(RESPONSES_DIR, f"{int(time.time())}_{gtin}.txt")

    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(f"# SCAN: {scan_path}\n")
        f.write(f"# PROMPT: {prompt_path}\n")
        f.write(f"# MODEL: {model_used}\n")
        f.write(f"# ELAPSED: {elapsed:.1f}s\n")
        f.write(f"# GTIN: {gtin}\n")
        f.write(f"#\n")
        f.write(text)

    print(f"Saved  : {out_file}")
    print()
    print("--- RAW RESPONSE ---")
    print(text)
    return out_file


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Stage 1: Send scan file to LLM router, save raw response."
    )
    parser.add_argument('scan',
        help="Path to scan file (logs/scans/*.txt)")
    parser.add_argument('--prompt', default=DEFAULT_PROMPT,
        help=f"Prompt template (default: extract_v1.txt)")
    parser.add_argument('--provider', default='gemini',
        choices=['gemini', 'claude', 'openai'],
        help="LLM provider (default: gemini)")
    parser.add_argument('--timeout', type=float, default=60.0,
        help="Request timeout in seconds (default: 60)")
    args = parser.parse_args()
    run(args.scan, args.prompt, args.provider, args.timeout)
