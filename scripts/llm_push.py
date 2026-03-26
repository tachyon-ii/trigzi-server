#!/usr/bin/env python3
from __future__ import annotations
"""
scripts/llm_push.py

Stage 1: Send a scan file to the LLM and write the raw response.

The raw response is saved to logs/llm_responses/<timestamp>_<gtin>.txt
for processing by llm_pull.py (Stage 2).

Usage:
    ./scripts/llm_push.py logs/scans/1743000000_9310077217814_ocr.txt
    ./scripts/llm_push.py logs/scans/1743000000_9310077217814_ocr.txt --prompt prompts/v2.txt
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

RESPONSES_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs', 'llm_responses')
DEFAULT_PROMPT = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'extract_v1.txt')

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL   = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={{api_key}}"
)


def load_scan(path: str) -> dict:
    """Parse a scan file into its component parts."""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    parts = {}
    lines = content.split('\n')

    # Extract header fields
    for line in lines:
        for key in ('GTIN', 'TIMESTAMP', 'SOURCE'):
            if line.startswith(f'{key}:'):
                parts[key.lower()] = line.split(':', 1)[1].strip()

    # Extract sections
    for section in ('FRONT OF PACKAGE', 'NUTRITION & INGREDIENTS', 'INGREDIENTS'):
        marker = f'=== {section} ==='
        if marker in content:
            after = content.split(marker, 1)[1]
            # Take until next section or end
            if '===' in after:
                text = after.split('===', 1)[0]
            else:
                text = after
            parts[section.lower().replace(' ', '_').replace('&_', '')] = text.strip()

    return parts


def load_prompt(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def call_gemini(prompt: str, api_key: str) -> str:
    """Call Gemini with a plain text prompt, return raw text response."""
    url     = GEMINI_URL.format(api_key=api_key)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
        }
    }
    data = json.dumps(payload).encode('utf-8')
    req  = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'}
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode('utf-8'))
        return (body
                .get('candidates', [{}])[0]
                .get('content', {})
                .get('parts', [{}])[0]
                .get('text', ''))


def run(scan_path: str, prompt_path: str) -> str:
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)

    print(f"Scan   : {scan_path}")
    print(f"Prompt : {prompt_path}")

    scan   = load_scan(scan_path)
    prompt_template = load_prompt(prompt_path)

    # Build the full prompt
    prompt = prompt_template.format(
        text_front     = scan.get('front_of_package', ''),
        text_nutrition = scan.get('nutrition_ingredients', '') or scan.get('ingredients', ''),
    )

    print(f"Calling {GEMINI_MODEL}...")
    t0       = time.time()
    response = call_gemini(prompt, api_key)
    elapsed  = time.time() - t0
    print(f"Response in {elapsed:.1f}s ({len(response)} chars)")

    # Save raw response
    os.makedirs(RESPONSES_DIR, exist_ok=True)
    gtin     = scan.get('gtin', 'unknown')
    out_file = os.path.join(RESPONSES_DIR, f"{int(time.time())}_{gtin}.txt")

    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(f"# SCAN: {scan_path}\n")
        f.write(f"# PROMPT: {prompt_path}\n")
        f.write(f"# MODEL: {GEMINI_MODEL}\n")
        f.write(f"# ELAPSED: {elapsed:.1f}s\n")
        f.write(f"# GTIN: {gtin}\n")
        f.write(f"#\n")
        f.write(response)

    print(f"Saved  : {out_file}")
    print()
    print("--- RAW RESPONSE ---")
    print(response)
    return out_file


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Stage 1: Send scan file to LLM, save raw response."
    )
    parser.add_argument('scan', help="Path to scan file (logs/scans/*.txt)")
    parser.add_argument('--prompt', default=DEFAULT_PROMPT,
                        help=f"Prompt template (default: {DEFAULT_PROMPT})")
    args = parser.parse_args()
    run(args.scan, args.prompt)
