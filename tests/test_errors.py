# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestGTINNormalisation)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument
"""
=============================================================================
Module:        Test — LLM Error Taxonomy
Location:      tests/test_errors.py
Description:   Exercises core.llm.errors.LLMError to verify the
               failoverable/terminal classification holds for each
               error factory. The router relies on is_failoverable to
               decide whether to advance to the next provider in the
               fallback chain — getting that bit wrong silently breaks
               either resilience (terminal flagged failoverable) or
               cost-control (transient flagged terminal, never retried).

Architecture Note:
Three groups of LLMError factories under test:
  - Failoverable: rate_limited, server_error, decode_failed
  - Terminal:    invalid_request, encoding_failed
  - Diagnostic:  decode_failed also captures raw response for
                 post-mortem analysis without raising in the hot path.
=============================================================================
"""

import unittest
from core.llm.errors import LLMError


class TestLLMErrors(unittest.TestCase):

    def test_failoverable_errors(self):
        """Verify transient errors are flagged for router failover."""
        err = LLMError.rate_limited("Gemini", retry_after=30)
        self.assertTrue(err.is_failoverable)
        self.assertEqual(err.provider, "Gemini")
        self.assertIn("Retry after 30", str(err))

        err_server = LLMError.server_error("Claude", 500, "Internal Error")
        self.assertTrue(err_server.is_failoverable)

    def test_terminal_errors(self):
        """Verify malformed requests halt the failover chain."""
        err = LLMError.invalid_request("Missing text payload")
        self.assertFalse(err.is_failoverable)

        err_encoding = LLMError.encoding_failed("Bad base64")
        self.assertFalse(err_encoding.is_failoverable)

    def test_decode_failed_stores_raw_response(self):
        """Ensure raw unparsable JSON is captured for debugging but doesn't break the router."""
        raw_json = '{"safe": tr}'
        err = LLMError.decode_failed("OpenAI", raw=raw_json)
        self.assertTrue(err.is_failoverable)
        self.assertEqual(err.raw_response, raw_json)
