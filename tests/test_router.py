#
#  test/test_router.py
#  trigzi-backend
#
#  Functional tests for the LLM Router orchestrator.
#  Uses async mocking to simulate multi-provider environments.
#

import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from core.llm.router import router
from core.llm.errors import LLMError

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
