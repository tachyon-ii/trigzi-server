# test/test_filters.py
"""
Unit tests for the LLM request/response filtering layer.
"""

import unittest
from core.llm.filters import (
    OpenAIResponseFilter,
    ClaudeResponseFilter,
    GeminiResponseFilter,
    ClaudeRequestFilter,
)
from core.llm.filters.xml_filter import dict_to_xml


class TestResponseFilters(unittest.TestCase):
    """Validates the defensive parsing layer."""

    def setUp(self):
        self.openai_filter = OpenAIResponseFilter()
        self.claude_filter = ClaudeResponseFilter()
        self.gemini_filter = GeminiResponseFilter()

        self.clean_json    = '{"safe": true, "verdict": "Safe"}'
        self.markdown_json = f'```json\n{self.clean_json}\n```'
        self.dirty_markdown = f'Here is the result:\n```json\n{self.clean_json}\n```\nDone.'

    def test_markdown_stripping_regex(self):
        """Ensure the base filter handles various hallucinated wrappers."""
        self.assertEqual(
            self.openai_filter._strip_markdown(self.markdown_json),
            self.clean_json
        )
        self.assertEqual(
            self.openai_filter._strip_markdown(self.dirty_markdown),
            self.clean_json
        )
        self.assertEqual(
            self.openai_filter._strip_markdown('   { "a": 1 }   '),
            '{ "a": 1 }'
        )

    def test_openai_extraction(self):
        mock = {"choices": [{"message": {"content": self.dirty_markdown}}]}
        result = self.openai_filter.extract_json(mock, "OpenAI")
        self.assertEqual(result, self.clean_json)

    def test_claude_extraction(self):
        mock = {"content": [{"type": "text", "text": self.markdown_json}]}
        result = self.claude_filter.extract_json(mock, "Claude")
        self.assertEqual(result, self.clean_json)

    def test_gemini_extraction(self):
        mock = {"candidates": [{"content": {"parts": [{"text": self.clean_json}]}}]}
        result = self.gemini_filter.extract_json(mock, "Gemini")
        self.assertEqual(result, self.clean_json)


class TestRequestFilters(unittest.TestCase):
    """Validates payload construction, specifically XML framing for Claude."""

    def test_claude_xml_framing(self):
        f = ClaudeRequestFilter()
        payload = f.build_text_payload("Analyze this", "claude-sonnet")
        content = payload["messages"][0]["content"]
        self.assertTrue(content.startswith("<instructions>"))
        self.assertTrue(content.endswith("</instructions>"))
        self.assertIn("Analyze this", content)

    def test_dict_to_xml_recursion(self):
        data = {
            "profile": {"allergy": "Dairy", "severity": "High"},
            "task": "Scan"
        }
        # XMLFilter._serialize_to_xml indents nested elements for readability.
        # dict_to_xml is a shim over it — indentation is expected behaviour.
        expected = (
            "<profile>\n"
            "  <allergy>Dairy</allergy>\n"
            "  <severity>High</severity>\n"
            "</profile>\n"
            "<task>Scan</task>"
        )
        self.assertEqual(dict_to_xml(data), expected)


if __name__ == "__main__":
    unittest.main()
