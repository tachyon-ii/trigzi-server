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
