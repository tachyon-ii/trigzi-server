import unittest
import threading
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
        for t in threads: t.start()
        for t in threads: t.join()
        
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
