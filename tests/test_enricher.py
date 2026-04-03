#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_enricher.py
#
#  Unit tests for core/enricher.py
#  All LLM router calls, DB calls, and file I/O are mocked.
#

import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FOOD_RECORD = {
    "gtin":            "0070177161170",
    "source":          "off",
    "name":            "Test Crackers",
    "brand":           "Test Brand",
    "category":        "Pantry",
    "raw_ingredients": "Wheat flour, Salt, Palm oil",
    "nutrition_100g":  {"energy_kj": 1800.0, "protein_g": 8.0},
    "health_star_rating": 3.0,
    "_source_name":    "off",
    "_enrichment_llm": None,
    "clinical_profile": None,
}

NON_FOOD_RECORD = dict(FOOD_RECORD, category="Cleaning & Laundry", name="Dish Soap")

MOCK_CLINICAL_PROFILE = {
    "estimated_health_star": None,
    "fodmap_rating":         1,
    "coeliac_rating":        3,
    "histamine_rating":      0,
    "allergen_warnings":     ["Gluten"],
    "health_summary":        "Contains gluten; unsuitable for coeliacs.",
}

MOCK_ROUTER_RESPONSE = {
    "result":       MOCK_CLINICAL_PROFILE,
    "model":        "gemini-2.5-flash",
    "provider":     "Gemini",
    "latency_ms":   410,
    "was_fallback": False,
}


# ---------------------------------------------------------------------------
# enrich() -- food products
# ---------------------------------------------------------------------------

class TestEnrichFood(unittest.IsolatedAsyncioTestCase):

    async def _run_enrich(self, record, router_response=None, enrichment_id=7):
        """Helper: patch all external dependencies and call enrich()."""
        from core.enricher import enrich

        router_mock = AsyncMock(return_value=router_response or MOCK_ROUTER_RESPONSE)

        with patch("core.enricher.router.analyze",        router_mock), \
             patch("core.enricher.get_or_create_enrichment", return_value=enrichment_id), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            return await enrich(record), router_mock

    async def test_returns_enriched_record(self):
        result, _ = await self._run_enrich(FOOD_RECORD)
        self.assertIsNotNone(result.get("clinical_profile"))
        self.assertEqual(result["clinical_profile"], MOCK_CLINICAL_PROFILE)

    async def test_sets_enrichment_llm_model(self):
        result, _ = await self._run_enrich(FOOD_RECORD)
        self.assertEqual(result["_enrichment_llm"], "gemini-2.5-flash")

    async def test_calls_router_with_product_payload(self):
        _, mock = await self._run_enrich(FOOD_RECORD)
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("product", payload)
        self.assertEqual(payload["product"]["gtin"], FOOD_RECORD["gtin"])

    async def test_calls_get_or_create_enrichment(self):
        from core.enricher import enrich, PROMPT_VER

        with patch("core.enricher.router.analyze", AsyncMock(return_value=MOCK_ROUTER_RESPONSE)), \
             patch("core.enricher.get_or_create_enrichment", return_value=7) as mock_enrich_id, \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            await enrich(FOOD_RECORD)

        mock_enrich_id.assert_called_once()
        kwargs = mock_enrich_id.call_args.kwargs if mock_enrich_id.call_args.kwargs \
                 else dict(zip(["task","llm_model","prompt_ver","prompt_text"],
                               mock_enrich_id.call_args.args))
        self.assertEqual(kwargs.get("task") or mock_enrich_id.call_args.args[0], "product")

    async def test_saves_to_db_with_enrichment_id(self):
        from core.enricher import enrich

        save_mock = AsyncMock()
        with patch("core.enricher.router.analyze", AsyncMock(return_value=MOCK_ROUTER_RESPONSE)), \
             patch("core.enricher.get_or_create_enrichment", return_value=42), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save", save_mock):
            await enrich(FOOD_RECORD)

        save_mock.assert_called_once()
        _, kwargs = save_mock.call_args
        self.assertEqual(kwargs.get("enrichment_id") or save_mock.call_args.args[1], 42)

    async def test_queues_for_validation_on_success(self):
        from core.enricher import enrich

        queue_mock = MagicMock()
        with patch("core.enricher.router.analyze", AsyncMock(return_value=MOCK_ROUTER_RESPONSE)), \
             patch("core.enricher.get_or_create_enrichment", return_value=7), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation", queue_mock), \
             patch("core.enricher._off.save"):
            await enrich(FOOD_RECORD)

        queue_mock.assert_called_once()

    async def test_router_failure_sets_failed_status(self):
        from core.enricher import enrich

        save_mock = AsyncMock()
        with patch("core.enricher.router.analyze", AsyncMock(side_effect=Exception("LLM down"))), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save", save_mock):
            result = await enrich(FOOD_RECORD)

        self.assertEqual(result["_enrichment_llm"], "FAILED")
        save_mock.assert_called_once()
        # enrichment_id must be None on failure
        call_kwargs = save_mock.call_args
        enrichment_id_arg = (call_kwargs.kwargs.get("enrichment_id")
                             if call_kwargs.kwargs
                             else call_kwargs.args[1] if len(call_kwargs.args) > 1
                             else None)
        self.assertIsNone(enrichment_id_arg)

    async def test_does_not_queue_for_validation_on_failure(self):
        from core.enricher import enrich

        queue_mock = MagicMock()
        with patch("core.enricher.router.analyze", AsyncMock(side_effect=Exception("down"))), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation", queue_mock), \
             patch("core.enricher._off.save"):
            await enrich(FOOD_RECORD)

        queue_mock.assert_not_called()


# ---------------------------------------------------------------------------
# enrich() -- non-food products
# ---------------------------------------------------------------------------

class TestEnrichNonFood(unittest.IsolatedAsyncioTestCase):

    async def test_non_food_skips_router(self):
        from core.enricher import enrich

        router_mock = AsyncMock()
        with patch("core.enricher.router.analyze", router_mock), \
             patch("core.enricher.get_or_create_enrichment", return_value=1), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            await enrich(NON_FOOD_RECORD)

        router_mock.assert_not_called()

    async def test_non_food_sets_nop_profile(self):
        from core.enricher import enrich

        with patch("core.enricher.router.analyze", AsyncMock()), \
             patch("core.enricher.get_or_create_enrichment", return_value=1), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            result = await enrich(NON_FOOD_RECORD)

        self.assertIsNotNone(result["clinical_profile"])
        self.assertEqual(result["clinical_profile"]["fodmap_rating"], -1)
        self.assertIn("Non-food", result["clinical_profile"]["health_summary"])

    async def test_non_food_llm_model_is_nop(self):
        from core.enricher import enrich

        with patch("core.enricher.router.analyze", AsyncMock()), \
             patch("core.enricher.get_or_create_enrichment", return_value=1), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            result = await enrich(NON_FOOD_RECORD)

        self.assertEqual(result["_enrichment_llm"], "NOP")


# ---------------------------------------------------------------------------
# _queue_for_validation() -- isolated
# ---------------------------------------------------------------------------

class TestQueueForValidation(unittest.TestCase):

    def test_writes_json_line(self):
        from core.enricher import _queue_for_validation
        import json

        written = []
        m = mock_open()
        m.return_value.__enter__.return_value.write = lambda s: written.append(s)

        with patch("builtins.open", m), \
             patch("os.makedirs"):
            _queue_for_validation(FOOD_RECORD)

        self.assertTrue(any(FOOD_RECORD["gtin"] in line for line in written))

    def test_silently_handles_write_error(self):
        """File write failures must not propagate -- enrichment should continue."""
        from core.enricher import _queue_for_validation

        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch("os.makedirs"):
            _queue_for_validation(FOOD_RECORD)  # must not raise


if __name__ == "__main__":
    unittest.main()
