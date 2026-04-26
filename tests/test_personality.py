"""
=============================================================================
Module:        Test — Personality / Persona Matrix
Location:      tests/test_personality.py
Description:   Hardcore verbatim contract tests for core/personality.py
               — the Persona Matrix Engine that picks an LLM
               personality (clinical / balanced / sassy) plus an
               optional kids-mode safety modifier based on user
               settings.

Architecture Note:
These tests intentionally hardcode the EXACT expected output strings
rather than importing constants from the module under test. Pulling
the constants live would mean a malicious or accidental edit to
core/personality.py would silently make the tests pass. By
hardcoding, any drift in the persona text triggers an immediate
test failure — enforcing strict version control over the LLM's
behavioural boundaries (especially the kids-mode safety prepend,
which must NOT be bypassable).

The hardcoded persona strings exceed the 140-char line limit
because they are verbatim contract text — wrapping mid-string would
change the value being asserted. Per-line line-too-long disables
mark this intent.
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestPersonalityEngineVerbatim)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
#   duplicate-code         — sys.path bootstrap try/except pattern is shared across
#                            tests that need to import from project-relative paths;
#                            extracting it would create a tests/ helper module
#                            that would itself need a bootstrap. Accepting the dup.
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument,duplicate-code

from __future__ import annotations

import os
import sys
import unittest

# sys.path bootstrap so this file works whether pytest runs from the project
# root or directly. The try/except wrapping declares to pylint that the
# project import after the path mutation is intentional.
try:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from core.personality import get_persona_instruction  # pylint: disable=ungrouped-imports
except ImportError as exc:
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)


class TestPersonalityEngineVerbatim(unittest.TestCase):

    # ==============================================================================
    # EXACT STRING CONTRACTS
    # Do not change these unless the core product requirement for the persona changes.
    # ==============================================================================

    # pylint: disable=line-too-long
    KIDS_MODIFIER = "You are interacting with a child between 8 and 14 years old. You must use age-appropriate, highly encouraging language, avoid complex medical jargon, and ensure all advice is gentle and safe. "

    CLINICAL_ADULT = "A clinical, highly professional, and empathetic dietary assistant. Provide strictly factual, objective advice. ZERO sass, zero sarcasm, and zero jokes. Prioritize clarity and safety."

    BALANCED_ADULT = "A friendly, conversational, and slightly sassy dietary assistant. Balance helpful, accurate dietary advice with a warm, witty tone."

    SASSY_ADULT = "A sassy, highly observant, and unfiltered dietary assistant. Playfully roast the user, be witty, and deliver your accurate dietary advice with maximum sass and humor."
    # pylint: enable=line-too-long


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
