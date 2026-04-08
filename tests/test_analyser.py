#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_analyser.py
#
#  Unit tests for core/analyser.py
#  All LLM router calls are mocked -- no real API calls made.
#

import json
import unittest
from unittest.mock import AsyncMock, patch

import logging
logging.disable(logging.CRITICAL) # Mute expected exception logs during test runs

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

# For product and meal (which expect JSON string back from router)
MOCK_ROUTER_JSON_RESPONSE = {
    "result":       json.dumps(MOCK_RESULT),
    "model":        "gemini-2.5-flash",
    "provider":     "Gemini",
    "latency_ms":   320,
    "was_fallback": False,
}

# For menu (which expects flat text back from router)
MOCK_ROUTER_MENU_RESPONSE = {
    "result":       "Dish: Pad Thai\nListed: noodles, egg\nSuspected: \n---",
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
            mock.return_value = MOCK_ROUTER_JSON_RESPONSE
            result = await analyse_product(
                gtin="0070177161170",
                text_front="Brand Name Crackers",
                text_nutrition="Wheat, Salt, Oil"
            )
        self.assertEqual(result, MOCK_RESULT)

    async def test_payload_contains_combined_text(self):
        from core.analyser import analyse_product
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_JSON_RESPONSE
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

    async def test_uses_config_routing(self):
        """Optimization strategy should be pulled from the dynamic config."""
        from core.analyser import analyse_product
        mock_cfg = {"models": ["gemini"], "optimize": "accuracy", "timeout": 30}
        
        with patch("core.analyser.llm_config.task_config", return_value=mock_cfg), \
             patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_JSON_RESPONSE
            await analyse_product("x", "front", "nutrition")
        
        self.assertEqual(mock.call_args.kwargs["optimize"], "accuracy")


# ---------------------------------------------------------------------------
# analyse_meal
# ---------------------------------------------------------------------------

class TestAnalyseMeal(unittest.IsolatedAsyncioTestCase):

    async def test_returns_result_on_success(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_JSON_RESPONSE
            result = await analyse_meal(image="base64data==", profile="Low FODMAP")
        self.assertEqual(result, MOCK_RESULT)

    async def test_payload_uses_image_base64_key(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_JSON_RESPONSE
            await analyse_meal(image="base64data==", profile="")
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("image_base64", payload)
        self.assertEqual(payload["image_base64"], "base64data==")

    async def test_profile_forwarded_to_router(self):
        from core.analyser import analyse_meal
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_JSON_RESPONSE
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
            mock.return_value = MOCK_ROUTER_MENU_RESPONSE
            result = await analyse_menu(text="Pad Thai $18\nGreen Curry $20")
            
        self.assertEqual(result["type"], "menu")
        self.assertEqual(result["items"][0]["name"], "Pad Thai")
        self.assertEqual(result["items"][0]["listed_ingredients"], ["noodles", "egg"])

    async def test_payload_uses_menu_text_key(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_MENU_RESPONSE
            await analyse_menu(text="menu text")
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("menu_text", payload)
        self.assertEqual(payload["menu_text"], "menu text")

    async def test_returns_none_for_empty_text(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            result = await analyse_menu(text="")
        self.assertIsNone(result)
        mock.assert_not_called()

    async def test_returns_none_on_router_exception(self):
        from core.analyser import analyse_menu
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("provider down")
            result = await analyse_menu(text="some menu")
        self.assertIsNone(result)

    async def test_uses_config_models(self):
        """Failover chain should pull models from the JSON config."""
        from core.analyser import analyse_menu
        mock_cfg = {"models": ["m1", "m2"], "optimize": "failover", "timeout": 10}
        
        with patch("core.analyser.llm_config.task_config", return_value=mock_cfg), \
             patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_ROUTER_MENU_RESPONSE
            await analyse_menu(text="menu")
            
        models = mock.call_args.kwargs["model_strings"]
        self.assertEqual(list(models), ["m1", "m2"])

# ---------------------------------------------------------------------------
# chat_assistant
# ---------------------------------------------------------------------------

class TestChatAssistant(unittest.IsolatedAsyncioTestCase):

    async def test_string_system_context_handled_gracefully(self):
        """Verify that passing a string for system_context doesn't crash the parser."""
        from core.analyser import chat_assistant
        
        mock_router_response = {
            "result": "Response: Hello James!",
            "model": "gemini-2.5-flash",
            "provider": "Gemini"
        }
        
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = mock_router_response
            
            # THE SMOKING GUN: Passing a raw string instead of a dict
            malformed_context = "[ENVIRONMENTAL FACTS]\nuser_localtime: Australia/Sydney\n"
            
            # 🛡️ THE FIX: Unpack the tuple (text, action)
            text, action = await chat_assistant(
                system_context=malformed_context, 
                history=[], 
                message="My name is James!"
            )
            
            # With the fix, it should gracefully wrap the string and return the payload
            self.assertEqual(text, "Hello James!")
            self.assertIsNone(action)
            
            # Verify the string was wrapped in a dictionary correctly before hitting the prompt builder
            payload = mock.call_args.kwargs["payload"]
            self.assertIn("Australia/Sydney", payload["prompt"])

    async def test_returns_error_on_router_exception(self):
        """Verify the parser degrades gracefully if the LLM fails."""
        from core.analyser import chat_assistant
        
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("LLM Timeout")
            
            # 🛡️ THE FIX: Unpack the tuple here as well
            text, action = await chat_assistant({"dietary_profile": "Vegan"}, [], "Hello")
            
        self.assertIsNone(text)
        self.assertIsNone(action)

# ---------------------------------------------------------------------------
# onboarding_assistant
# ---------------------------------------------------------------------------

class TestOnboardingAssistant(unittest.IsolatedAsyncioTestCase):

    async def test_successful_parsing(self):
        """Verify the flat-text protocol is perfectly diced into SSE event dictionaries."""
        from core.analyser import onboarding_assistant
        
        mock_raw_response = (
            "Message: Fine, I'll call you Zesty Koala. Ready for the tour?\n"
            "Fact: user_name=Zesty Koala\n"
            "Action: set_name|Zesty Koala\n"
            "---"
        )
        
        mock_router_response = {
            "result": mock_raw_response,
            "model": "gemini-2.5-flash",
            "provider": "Gemini",
        }

        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = mock_router_response
            
            events, text_content = await onboarding_assistant("Whatever.", "Zesty Koala")
            
            # Check the raw text extraction for the emoji pipeline
            self.assertEqual(text_content, "Fine, I'll call you Zesty Koala. Ready for the tour?")
            
            # Check the parsed events
            self.assertEqual(len(events), 3)
            self.assertEqual(events[0], {"event": "text", "data": {"content": "Fine, I'll call you Zesty Koala. Ready for the tour?"}})
            self.assertEqual(events[1], {"event": "fact", "data": {"key": "user_name", "value": "Zesty Koala"}})
            self.assertEqual(events[2], {"event": "action", "data": {"tool": "set_name", "param": "Zesty Koala"}})

    async def test_parameterless_action_parsing(self):
        """Verify actions without a pipe parameter are handled cleanly."""
        from core.analyser import onboarding_assistant
        
        mock_raw_response = "Action: start_tour\n---"
        
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.return_value = {"result": mock_raw_response}
            events, _ = await onboarding_assistant("Yes", "Zesty Koala")
            
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0], {"event": "action", "data": {"tool": "start_tour", "param": ""}})

    async def test_returns_error_for_empty_message(self):
        """Verify the pipeline bails early if no message is provided."""
        from core.analyser import onboarding_assistant
        
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            events, text = await onboarding_assistant("", "Zesty Koala")
            
        self.assertEqual(events[0]["event"], "error")
        self.assertEqual(text, "")
        mock.assert_not_called()

    async def test_returns_error_on_router_exception(self):
        """Verify the parser degrades gracefully if the LLM fails or times out."""
        from core.analyser import onboarding_assistant
        
        with patch("core.analyser.router.analyze", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("LLM Timeout")
            events, text = await onboarding_assistant("Hello", "Zesty Koala")
            
        self.assertEqual(events[0]["event"], "error")
        self.assertEqual(text, "")

if __name__ == "__main__":
    unittest.main()
