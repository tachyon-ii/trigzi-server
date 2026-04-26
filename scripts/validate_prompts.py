#!/usr/bin/env python3
"""
=============================================================================
Module:        Prompt Contract Validator
Location:      scripts/validate_prompts.py
Description:   Validates every .txt file in prompts/ against the strict
               [OUTPUT] schema contract enforced by core.llm.validator.
               Non-conforming prompts cause a non-zero exit so this can
               be wired into pre-deploy hooks.

Architecture Note:
The check is intentionally fail-closed — any single broken prompt blocks
the whole batch. Prompts must declare their [OUTPUT] block in a form
SchemaValidator can parse, or production routing will reject responses
silently. Run this before every deploy that touches prompts/.

Usage:
    ./scripts/validate_prompts.py
=============================================================================
"""

import glob
import os
import sys

try:
    from core.llm.validator import SchemaValidator
except ImportError:
    # Allow running from project root without installing the package
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from core.llm.validator import SchemaValidator


def run():
    """Walk prompts/*.txt, run each through SchemaValidator, exit non-zero on any failure."""
    prompts_dir = os.path.join(os.path.dirname(__file__), '..', 'prompts')
    prompt_files = glob.glob(os.path.join(prompts_dir, '*.txt'))

    if not prompt_files:
        print("❌ No .txt files found in prompts directory.")
        sys.exit(1)

    print(f"🔍 Validating {len(prompt_files)} prompts against the [OUTPUT] contract...\n")

    failures = 0
    for filepath in prompt_files:
        filename = os.path.basename(filepath)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        try:
            SchemaValidator.validate_prompt_contract(content)
            print(f"  ✅ {filename} passed.")
        except ValueError as e:
            print(f"  ❌ {filename} FAILED:\n     {e}")
            failures += 1

    print("\n" + "="*40)
    if failures == 0:
        print("🎉 All prompts valid. Ready for production.")
        sys.exit(0)
    else:
        print(f"🚨 {failures} prompt(s) failed validation. Fix them before deploying.")
        sys.exit(1)


if __name__ == '__main__':
    run()
