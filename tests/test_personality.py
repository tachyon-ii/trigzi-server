#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_personality.py
#  trigzi-backend
#
#  PURPOSE:
#    Hardcore verbatim contract tests for the Persona Matrix Engine.
#    These tests do NOT dynamically pull constants from the module; they 
#    hardcode the EXACT expected output strings. 
#
#    If a developer alters a persona prompt in core/personality.py out of 
#    malice, ignorance, or accidental keystrokes, the CI/CD pipeline WILL fail. 
#    This enforces strict version control over the LLM's behavioral boundaries.
#
#  DEPENDENCIES:
#    unittest, core.personality
#

import unittest
import sys, os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.personality import get_persona_instruction

class TestPersonalityEngineVerbatim(unittest.TestCase):

    # ==============================================================================
    # EXACT STRING CONTRACTS
    # Do not change these unless the core product requirement for the persona changes.
    # ==============================================================================
    
    KIDS_MODIFIER = "You are interacting with a child between 8 and 14 years old. You must use age-appropriate, highly encouraging language, avoid complex medical jargon, and ensure all advice is gentle and safe. "
    
    CLINICAL_ADULT = "A clinical, highly professional, and empathetic dietary assistant. Provide strictly factual, objective advice. ZERO sass, zero sarcasm, and zero jokes. Prioritize clarity and safety."
    
    BALANCED_ADULT = "A friendly, conversational, and slightly sassy dietary assistant. Balance helpful, accurate dietary advice with a warm, witty tone."
    
    SASSY_ADULT = "A sassy, highly observant, and unfiltered dietary assistant. Playfully roast the user, be witty, and deliver your accurate dietary advice with maximum sass and humor."


    # MARK: - Baseline / Fallback Verbatim Tests

    def test_verbatim_default_fallback(self):
        """An empty system context MUST default strictly to the Balanced Adult persona."""
        result = get_persona_instruction({})
        self.assertEqual(result, self.BALANCED_ADULT)

    def test_verbatim_malformed_context_fallback(self):
        """Garbage context data MUST fail gracefully to the Balanced Adult persona."""
        result = get_persona_instruction({'attitude_level': 'drop table users', 'experience_mode': -99})
        self.assertEqual(result, self.BALANCED_ADULT)


    # MARK: - Adult (Advanced/Standard) Verbatim Tests

    def test_verbatim_adult_clinical(self):
        """Attitude 0 + Mode 2 MUST yield the exact Clinical Adult string."""
        result = get_persona_instruction({'attitude_level': 0, 'experience_mode': 2})
        self.assertEqual(result, self.CLINICAL_ADULT)

    def test_verbatim_adult_balanced(self):
        """Attitude 1 + Mode 2 MUST yield the exact Balanced Adult string."""
        result = get_persona_instruction({'attitude_level': 1, 'experience_mode': 2})
        self.assertEqual(result, self.BALANCED_ADULT)

    def test_verbatim_adult_sassy(self):
        """Attitude 2 + Mode 2 MUST yield the exact Sassy Adult string."""
        result = get_persona_instruction({'attitude_level': 2, 'experience_mode': 2})
        self.assertEqual(result, self.SASSY_ADULT)


    # MARK: - Kids Mode Verbatim Tests (The Safety Net)

    def test_verbatim_kids_clinical(self):
        """Attitude 0 + Mode 0 MUST prepend the exact Kids modifier to the Clinical string."""
        expected = self.KIDS_MODIFIER + self.CLINICAL_ADULT
        result = get_persona_instruction({'attitude_level': 0, 'experience_mode': 0})
        self.assertEqual(result, expected)

    def test_verbatim_kids_balanced(self):
        """Attitude 1 + Mode 0 MUST prepend the exact Kids modifier to the Balanced string."""
        expected = self.KIDS_MODIFIER + self.BALANCED_ADULT
        result = get_persona_instruction({'attitude_level': 1, 'experience_mode': 0})
        self.assertEqual(result, expected)

    def test_verbatim_kids_sassy(self):
        """
        CRITICAL SAFETY TEST:
        Attitude 2 + Mode 0 MUST prepend the exact Kids modifier to the Sassy string.
        This proves that a user requesting maximum sass while in Kids Mode cannot 
        bypass the child-safety prompt injection.
        """
        expected = self.KIDS_MODIFIER + self.SASSY_ADULT
        result = get_persona_instruction({'attitude_level': 2, 'experience_mode': 0})
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
