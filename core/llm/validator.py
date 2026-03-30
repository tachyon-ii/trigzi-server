#!/usr/bin/env python3
from __future__ import annotations
#
#  core/llm/validator.py
#  trigzi-backend
#
#  Centralized structural validation for LLM prompts and responses.
#  Enforces the universal flat-text contract: [OUTPUT] ... ---
#

import re
import logging

logger = logging.getLogger(__name__)

class SchemaValidator:
    
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
                "'[OUTPUT]' block or the '---' terminator.\n"
                f"Prompt snippet: {prompt_text[:200]}..."
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
                        f"SchemaValidator Failed: Missing required key '{key}:' in block {index}.\n"
                        f"Expected Keys: {expected_keys}\n"
                        f"Offending Block:\n{block}"
                    )
                    return False
                    
        return True
