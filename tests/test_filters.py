# test/test_filters.py

#!/usr/bin/env python3
"""
Unit tests for the LLM request/response filtering layer.
"""

import unittest
from core.llm.filters.request_filter import ClaudeRequestFilter, OpenAIRequestFilter, GeminiRequestFilter
from core.llm.filters.response_filter import ClaudeResponseFilter, OpenAIResponseFilter, GeminiResponseFilter

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
