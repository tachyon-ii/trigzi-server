#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_analyser.py
#
#  Unit tests for core/analyser.py
#  All LLM router calls are mocked -- no real API calls made.
#

import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MOCK_RESULT = {
    "type": "meal_photo",
    "items": [{"name": "Pad Thai", "safe": True, "verdict": "Safe",
               "summary": "Generally safe.", "warnings": [],
               "ingredients": ["noodles", "egg"], "flaggedIngredients": [],
               "detailedReason": "No flagged ingredients."}]
}

MOCK_ROUTER_RESPONSE = {
    "result":       MOCK_RESULT,
    "model":        "gemini-2.5-flash",
    "provider":     "Gemini",
    "latency_ms":   320,
    "was_fallback": False,
}


# ---------------------------------------------------------------------------
# analyse_product
# ---------------------------------------------------------------------------

class TestAnalyseProduct(unittest.IsolatedAsyncioTestCase):

    async def test_returns_result_on_success(self):
        from core.analyser import analyse_product
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            result = await analyse_product(
                gtin="0070177161170",
                text_front="Brand Name Crackers",
                text_nutrition="Wheat, Salt, Oil"
            )
        self.assertEqual(result, MOCK_RESULT)

    async def test_payload_contains_combined_text(self):
        """Both front and nutrition text should be included in the payload."""
        from core.analyser import analyse_product
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            await analyse_product(
                gtin="0070177161170",
                text_front="Front label text",
                text_nutrition="Nutrition text"
            )
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("Front label text",  payload["text"])
        self.assertIn("Nutrition text",    payload["text"])

    async def test_returns_none_when_both_texts_empty(self):
        from core.analyser import analyse_product
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            result = await analyse_product(gtin="0070177161170", text_front="", text_nutrition="")
        self.assertIsNone(result)
        mock.assert_not_called()

    async def test_returns_none_on_router_exception(self):
        from core.analyser import analyse_product
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("Router failed")
            result = await analyse_product(
                gtin="0070177161170",
                text_front="something",
                text_nutrition=""
            )
        self.assertIsNone(result)

    async def test_uses_accuracy_optimize(self):
        from core.analyser import analyse_product
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            await analyse_product("x", "front", "nutrition")
        self.assertEqual(mock.call_args.kwargs["optimize"], "accuracy")


# ---------------------------------------------------------------------------
# analyse_meal
# ---------------------------------------------------------------------------

class TestAnalyseMeal(unittest.IsolatedAsyncioTestCase):

    async def test_returns_result_on_success(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            result = await analyse_meal(image="base64data==", profile="Low FODMAP")
        self.assertEqual(result, MOCK_RESULT)

    async def test_payload_uses_image_base64_key(self):
        """Meal analysis must send image_base64 so base.py routes to multimodal."""
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            await analyse_meal(image="base64data==", profile="")
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("image_base64", payload)
        self.assertEqual(payload["image_base64"], "base64data==")

    async def test_profile_forwarded_to_router(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            await analyse_meal(image="data==", profile="Dairy Free")
        self.assertEqual(mock.call_args.kwargs["profile"], "Dairy Free")

    async def test_returns_none_for_empty_image(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            result = await analyse_meal(image="", profile="")
        self.assertIsNone(result)
        mock.assert_not_called()

    async def test_returns_none_on_router_exception(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("timeout")
            result = await analyse_meal(image="data==", profile="")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# analyse_menu
# ---------------------------------------------------------------------------

class TestAnalyseMenu(unittest.IsolatedAsyncioTestCase):

    async def test_returns_result_on_success(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            result = await analyse_menu(text="Pad Thai $18\nGreen Curry $20", profile="Nut allergy")
        self.assertEqual(result, MOCK_RESULT)

    async def test_payload_uses_text_key(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            await analyse_menu(text="menu text", profile="")
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("text", payload)
        self.assertEqual(payload["text"], "menu text")

    async def test_returns_none_for_empty_text(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            result = await analyse_menu(text="", profile="")
        self.assertIsNone(result)
        mock.assert_not_called()

    async def test_returns_none_on_router_exception(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("provider down")
            result = await analyse_menu(text="some menu", profile="")
        self.assertIsNone(result)

    async def test_uses_three_default_models(self):
        """Failover chain should include all three providers."""
        from core.analyser import analyse_menu, DEFAULT_MODELS
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_RESPONSE
            await analyse_menu(text="menu", profile="")
        models = mock.call_args.kwargs["model_strings"]
        self.assertEqual(set(models), set(DEFAULT_MODELS))


if __name__ == "__main__":
    unittest.main()
