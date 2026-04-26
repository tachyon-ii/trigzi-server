#!/usr/bin/env python3
"""
=============================================================================
Module:        Schema Validator
Location:      core/llm/validator.py
Description:   Centralised structural validation for LLM prompts and responses.
               Enforces the universal flat-text contract — every prompt must
               declare [ACT AS], [TASK], [INSTRUCTIONS], and an [OUTPUT] block
               terminated by '---', and every response must conform to that
               schema before the analyser layer accepts it.

Architecture Note:
This is the contract-enforcement layer between the prompt-construction
templates in core.llm.skills and the response-decoding logic in
core.llm.filters.response_filter. It fails closed: a malformed prompt
or a non-conforming response yields ValueError or `False`, never silent
acceptance. The router treats a False return as a failoverable error.
=============================================================================
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

class SchemaValidator:
    """Static utility class enforcing the prompt/response flat-text contract.

    Three responsibilities:

    * :meth:`validate_prompt_contract` — pre-flight check that a prompt
      contains the four required directive blocks before it is sent.
    * :meth:`validate_stream` — boolean check that an LLM response conforms
      to the schema declared by its prompt's [OUTPUT] block.
    * :meth:`extract_blocks` — best-effort parser that pulls structured
      key/value data out of flat-text response blocks.

    All methods are stateless ``@staticmethod``s; the class exists purely
    to namespace the contract.
    """

    @staticmethod
    def validate_prompt_contract(prompt_text: str) -> None:
        """
        PRE-FLIGHT CHECK: Enforces the universal flat-text contract.
        All prompts MUST contain [ACT AS], [TASK], [INSTRUCTIONS], and
        an [OUTPUT] block terminated by '---'.
        """
        required_headers = ["[ACT AS]", "[TASK]", "[INSTRUCTIONS]"]

        # 1. Enforce the presence of all required metadata blocks
        for header in required_headers:
            # Anchored to the start of a line to prevent false positives if
            # the text just happens to contain the phrase.
            if not re.search(rf'^{re.escape(header)}', prompt_text, re.MULTILINE):
                raise ValueError(
                    f"CRITICAL ARCHITECTURE FAILURE: Prompt is missing the required '{header}' block."
                )

        # 2. Enforce the strict [OUTPUT] block and its terminator
        if not re.search(r'^\[OUTPUT\][\s\S]*?^---', prompt_text, re.MULTILINE):
            raise ValueError(
                "CRITICAL ARCHITECTURE FAILURE: Prompt violates the universal flat-text contract. "
                "It MUST contain an '[OUTPUT]' block terminated by '---' on a new line."
            )

    @staticmethod
    def validate_stream(prompt_text: str, response_text: str) -> bool:
        """
        Dynamically extracts the expected schema from the prompt and validates
        that the LLM response conforms to it.
        Fails closed if the prompt is malformed.
        """
        # 1. Strict extraction using the exact [OUTPUT] directive
        schema_match = re.search(r'^\[OUTPUT\]\s*(.*?)^---', prompt_text, re.MULTILINE | re.DOTALL)

        if not schema_match:
            logger.error(
                "SchemaValidator Failed: Prompt is missing the strictly required "
                "'[OUTPUT]' block or the '---' terminator.\nPrompt snippet: %s...",
                prompt_text[:200],
            )
            return False

        expected_keys = []
        for line in schema_match.group(1).strip().split('\n'):
            if ':' in line:
                expected_keys.append(line.split(':')[0].strip())

        if not expected_keys:
            logger.error("SchemaValidator Failed: [OUTPUT] block found, but no 'Key:' definitions were detected inside it.")
            return False

        # 2. Validate the response blocks
        blocks = [b.strip() for b in response_text.split("---") if b.strip()]

        if not blocks:
            logger.error("SchemaValidator Failed: LLM response contained no data blocks separated by '---'.")
            return False

        for index, block in enumerate(blocks):
            # Check if every expected key is present in the block
            for key in expected_keys:
                # Case-insensitive check in case the LLM messes up capitalization
                if f"{key.lower()}:" not in block.lower():
                    logger.error(
                        "SchemaValidator Failed: Missing required key '%s:' in block %d.\n"
                        "Expected Keys: %s\nOffending Block:\n%s",
                        key, index, expected_keys, block,
                    )
                    return False

        return True

    @staticmethod
    def extract_blocks(response_text: str, expected_keys: list[str]) -> list[dict]:
        """
        Safely extracts key-value pairs from flat-text blocks, handling multi-line values.
        """
        blocks = [b.strip() for b in response_text.split("---") if b.strip()]
        parsed_blocks = []

        # Regex: Start of line, capturing group for key, colon, lazy capture for value,
        # lookahead for the next key, the block terminator, or end of string.
        pattern = re.compile(
            r'^(?P<key>[A-Za-z_ -]+):\s*(?P<value>.*?)(?=\n^[A-Za-z_ -]+:|\n^---|\Z)',
            re.MULTILINE | re.DOTALL
        )

        expected_keys_lower = {k.lower() for k in expected_keys}

        for block in blocks:
            parsed_data = {}
            for match in pattern.finditer(block):
                key = match.group('key').strip().lower()
                if key in expected_keys_lower:
                    parsed_data[key] = match.group('value').strip()

            if parsed_data:
                parsed_blocks.append(parsed_data)

        return parsed_blocks
