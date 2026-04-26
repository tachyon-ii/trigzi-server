# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestGTINNormalisation)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument
"""
=============================================================================
Module:        Test — LLM Provider Config
Location:      tests/test_config.py
Description:   Exercises core.llm.config.LLMProviderConfig — the central
               registry for model strings, default tags, and cost
               estimation. Verifies the thread-safe singleton, model
               resolution (tag → wire string with sensible fallback),
               and per-token cost math against the per-million rates
               loaded from llm_providers.json.

Architecture Note:
The singleton matters because LLMProviderConfig holds the in-memory
provider/model catalogue; if double-initialised the two copies would
diverge on rate limits, defaults, etc. The threading test races 10
concurrent constructors to confirm the lock holds.

Cost estimation correctness matters for the cost-routing strategy
(core.llm.router._execute_cost) which picks the cheapest model that
can answer; if estimate_cost lies, the router optimises against
phantom prices.
=============================================================================
"""

import threading
import unittest

from core.llm.config import LLMProviderConfig


class TestLLMConfig(unittest.TestCase):

    def setUp(self):
        # The module-level instance is already initialized, but we can test the class
        self.config = LLMProviderConfig()

    def test_singleton_identity(self):
        """Verify the lock guarantees a single instance across threads."""
        instances = []
        def fetch():
            instances.append(LLMProviderConfig())

        threads = [threading.Thread(target=fetch) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(inst is instances[0] for inst in instances))

    def test_model_resolution(self):
        """Verify the router correctly resolves tags to wire-ready strings."""
        primary = self.config.primary_model("gemini")

        # None or empty should yield default
        self.assertEqual(self.config.resolve(None, "gemini"), primary)
        self.assertEqual(self.config.resolve("gemini", "gemini"), primary)

        # Known models pass through
        self.assertEqual(self.config.resolve("gemini-2.5-flash", "gemini"), "gemini-2.5-flash")

        # Unknown models pass through (letting the API reject them if invalid)
        self.assertEqual(self.config.resolve("gemini-experimental-x", "gemini"), "gemini-experimental-x")

    def test_cost_estimation(self):
        """Verify token math computes correctly against the per-million rates."""
        # gemini-2.5-flash: 0.30 in / 1.00 out
        cost = self.config.estimate_cost("gemini-2.5-flash", input_tokens=1_000_000, output_tokens=500_000)
        self.assertAlmostEqual(cost, 0.80, places=4)

        # Unknown models should return None for cost
        self.assertIsNone(self.config.estimate_cost("unknown-model", 100, 100))
