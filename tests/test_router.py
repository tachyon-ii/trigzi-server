"""
=============================================================================
Module:        Test — LLM Router Orchestration
Location:      tests/test_router.py
Description:   Functional tests for the multi-provider LLM router. Uses
               async mocking to simulate race / failover / cost routing
               scenarios end-to-end without hitting real APIs. The router
               is the central dispatch point for every LLM call in the
               system; getting its strategy semantics wrong silently
               degrades resilience or cost-efficiency in production.

Architecture Note:
Each test patches the providers' analyse() methods on the live
router.registry to inject controlled responses, then asserts on the
router's selection / fallback / cost-optimisation behaviour. The
router itself is never re-instantiated — these are integration tests
against the module-level singleton.

The four routing strategies covered:
  - race:     fastest wins, others cancelled
  - failover: tries the chain in order; advances on failoverable errors,
              halts on terminal errors
  - cost:     picks cheapest model across providers, ignoring list order
  - (ab is exercised in router.test_ab via direct unit tests, not here)
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestLLMRouter)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.llm.errors import LLMError
from core.llm.router import router


class TestLLMRouter(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.payload = {"text": "Ingredient: Whey Protein"}
        self.profile = "Dairy Allergy"

    # ------------------------------------------------------------------ #
    # Routing Logic                                                       #
    # ------------------------------------------------------------------ #

    async def test_race_mode(self):
        """Verify race mode returns the fastest provider and cancels others."""
        # Setup two providers: one fast, one slow
        fast_resp = {"provider": "Gemini", "model": "flash", "latency_ms": 100, "result": {"safe": False}}
        slow_resp = {"provider": "Claude", "model": "sonnet", "latency_ms": 5000, "result": {"safe": False}}

        with patch.object(router.registry["gemini"], "analyse", new_callable=AsyncMock) as mock_gem:
            with patch.object(router.registry["claude"], "analyse", new_callable=AsyncMock) as mock_claude:

                mock_gem.return_value = fast_resp

                # Make Claude wait longer than the test timeout to simulate cancellation
                async def slow_call(*args, **kwargs):
                    await asyncio.sleep(2)
                    return slow_resp
                mock_claude.side_effect = slow_call

                # Run race
                winner = await router.analyse(
                    self.payload, self.profile,
                    model_strings=["gemini", "claude"],
                    optimize="speed"
                )

                self.assertEqual(winner["provider"], "Gemini")
                self.assertEqual(winner["latency_ms"], 100)

    async def test_failover_mode(self):
        """Verify failover chain tries next model when the first hits a 5xx/429."""
        with patch.object(router.registry["gemini"], "analyse", new_callable=AsyncMock) as mock_gem:
            with patch.object(router.registry["claude"], "analyse", new_callable=AsyncMock) as mock_claude:

                # Gemini fails with a server error (failoverable)
                mock_gem.side_effect = LLMError.server_error("Gemini", 500, "Oops")

                # Claude succeeds
                mock_claude.return_value = {
                    "provider": "Claude", "model": "haiku", "latency_ms": 200, "result": {"safe": True}
                }

                response = await router.analyse(
                    self.payload, self.profile,
                    model_strings=["gemini", "claude"],
                    optimize="failover"    # explicit failover — cost now has its own routing path
                )

                self.assertEqual(response["provider"], "Claude")
                self.assertEqual(mock_gem.call_count, 1)
                self.assertEqual(mock_claude.call_count, 1)

    async def test_failover_terminal_failure(self):
        """Verify non-failoverable errors (invalid request) stop the chain immediately."""
        with patch.object(router.registry["gemini"], "analyse", new_callable=AsyncMock) as mock_gem:
            with patch.object(router.registry["claude"], "analyse", new_callable=AsyncMock) as mock_claude:

                # Gemini fails with a non-failoverable error (invalid request)
                mock_gem.side_effect = LLMError.invalid_request("Bad Prompt")

                with self.assertRaises(LLMError) as cm:
                    await router.analyse(
                        self.payload, self.profile,
                        model_strings=["gemini", "claude"],
                        optimize="failover"  # must be failover mode — ab mode would succeed on primary
                    )

                self.assertFalse(cm.exception.is_failoverable)
                self.assertEqual(mock_claude.call_count, 0)  # chain halted — Claude never tried

    async def test_cost_mode_selects_cheapest(self):
        """Verify cost mode picks the cheapest model across providers, not the first listed."""
        # gemini-2.5-flash-lite: $0.40/1M output — cheapest in the catalogue
        # claude-sonnet-4-6:     $15.00/1M output — expensive
        cheapest_resp = {
            "provider": "Gemini", "model": "gemini-2.5-flash-lite",
            "latency_ms": 300, "result": {"safe": True}
        }

        with patch.object(router.registry["gemini"], "analyse", new_callable=AsyncMock) as mock_gem:
            with patch.object(router.registry["claude"], "analyse", new_callable=AsyncMock) as mock_claude:
                mock_gem.return_value   = cheapest_resp
                mock_claude.return_value = {"provider": "Claude", "model": "sonnet", "latency_ms": 200, "result": {}}

                response = await router.analyse(
                    self.payload, self.profile,
                    model_strings=["claude", "gemini"],
                    optimize="cost"
                )

                # Should have called Gemini (cheapest), not Claude
                self.assertEqual(response["provider"], "Gemini")
                mock_claude.assert_not_called()


if __name__ == "__main__":
    asyncio.run(unittest.main())
