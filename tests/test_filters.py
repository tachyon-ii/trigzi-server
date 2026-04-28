# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestGTINNormalisation)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument
"""
=============================================================================
Module:        Test — LLM Filter Layer
Location:      tests/test_filters.py
Description:   Exercises the per-provider request and response filters
               that sit between the router and the wire format. Verifies
               request filters frame outgoing payloads correctly, and
               response filters extract the JSON content provider-by-
               provider as a "dumb pipe" (no markdown stripping —
               that's the analyser's job downstream).

Architecture Note:
The split: request filters build provider-specific payloads from the
router's neutral input; response filters do the inverse, extracting
the raw text content from each provider's nested response shape.
Markdown handling lives downstream in core.analyser, so these tests
explicitly assert the filter returns the exact string it received,
including the surrounding ```json fence.
=============================================================================
"""

import unittest

from core.llm.filters.request_filter import ClaudeRequestFilter
from core.llm.filters.response_filter import (
    ClaudeResponseFilter,
    GeminiResponseFilter,
    OpenAIResponseFilter,
)

# A foolproof mock prompt that passes the SchemaValidator's regex checks
VALID_PROMPT = """[ACT AS]
A unit test.
[TASK]
Pass the test.
[INSTRUCTIONS]
1. Do not fail.
[OUTPUT]
Message: ok
---"""


class TestResponseFilters(unittest.TestCase):
    def setUp(self):
        self.claude_filter = ClaudeResponseFilter()
        self.openai_filter = OpenAIResponseFilter()
        self.gemini_filter = GeminiResponseFilter()

        # Safely constructing markdown code blocks to avoid UI parser breakage
        tick3 = "`" * 3
        self.markdown_json = tick3 + "json\n{\"safe\": true, \"verdict\": \"Safe\"}\n" + tick3
        self.dirty_markdown = "Here is the result:\n" + tick3 + "json\n{\"safe\": true, \"verdict\": \"Safe\"}\n" + tick3 + "\nDone."
        self.clean_json = "{\"safe\": true, \"verdict\": \"Safe\"}"

    def test_claude_extraction(self):
        """Verify the filter acts as a dumb pipe and returns the exact string received."""
        mock = {"content": [{"type": "text", "text": self.markdown_json}]}
        # It should no longer strip markdown; the analyser handles that now
        result = self.claude_filter.extract_json(mock, "Claude")
        self.assertEqual(result, self.markdown_json)

    def test_openai_extraction(self):
        """Verify the filter acts as a dumb pipe and returns the exact string received."""
        mock = {"choices": [{"message": {"content": self.dirty_markdown}}]}
        result = self.openai_filter.extract_json(mock, "OpenAI")
        self.assertEqual(result, self.dirty_markdown)

    def test_gemini_extraction(self):
        """Verify standard extraction works without mutation."""
        mock = {"candidates": [{"content": {"parts": [{"text": self.clean_json}]}}]}
        result = self.gemini_filter.extract_json(mock, "Gemini")
        self.assertEqual(result, self.clean_json)


class TestRequestFilters(unittest.TestCase):
    def test_claude_payload_framing(self):
        """Verify the payload builds successfully when given a valid Schema contract."""
        f = ClaudeRequestFilter()

        # We must pass VALID_PROMPT to bypass the SchemaValidator
        payload = f.build_text_payload(VALID_PROMPT, "claude-sonnet")

        # Ensure the prompt actually made it into the payload
        messages = payload.get("messages", [])
        self.assertTrue(len(messages) > 0)
        self.assertIn("[ACT AS]", messages[0].get("content", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
