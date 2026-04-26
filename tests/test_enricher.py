"""
=============================================================================
Module:        Test — Enricher Pipeline
Location:      tests/test_enricher.py
Description:   Unit tests for core/enricher.py — the LLM-driven product
               enrichment pipeline that takes a raw OFF/Woolworths/Coles
               record and asks the router for clinical and dietary
               annotations (FODMAP, gluten, allergens, etc.). All LLM
               router calls, DB calls, and file I/O are mocked.

Architecture Note:
The MOCK_ROUTER_RESPONSE shape mirrors what BaseProvider.analyse()
actually returns:
  - ``result``        — the raw flat-text string from the LLM
  - ``parsed_blocks`` — the structured dict produced by
                        SchemaValidator.extract_blocks()

Tests cover four areas:
  - Happy path: enrich → router → save sequence
  - Field correctness: which kwargs go into router.analyse / get_or_create_enrichment
  - Clinical coercion: _coerce_clinical normalises LLM-emitted shapes
  - Validation queue: _queue_for_validation_sync writes JSONL safely
                      and survives I/O failures
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

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

# What the LLM emits (raw flat text) per prompts/enrich_product.txt
RAW_LLM_TEXT = (
    "Estimated Health Star: 3.0\n"
    "FODMAP Rating: 1\n"
    "Coeliac Rating: 3\n"
    "Histamine Rating: 0\n"
    "Allergens: Gluten\n"
    "Health Summary: Contains gluten; unsuitable for coeliacs.\n"
    "---"
)

# What SchemaValidator.extract_blocks produces from RAW_LLM_TEXT
PARSED_BLOCK = {
    "estimated health star": "3.0",
    "fodmap rating":         "1",
    "coeliac rating":        "3",
    "histamine rating":      "0",
    "allergens":             "Gluten",
    "health summary":        "Contains gluten; unsuitable for coeliacs.",
}

# The canonical clinical_profile after _coerce_clinical()
EXPECTED_CLINICAL_PROFILE = {
    "estimated_health_star": 3.0,
    "fodmap_rating":         1,
    "coeliac_rating":        3,
    "histamine_rating":      0,
    "allergen_warnings":     ["Gluten"],
    "health_summary":        "Contains gluten; unsuitable for coeliacs.",
}

# Shaped exactly like BaseProvider.analyse() return value
MOCK_ROUTER_RESPONSE = {
    "result":        RAW_LLM_TEXT,
    "parsed_blocks": [PARSED_BLOCK],
    "model":         "gemini-2.5-flash",
    "provider":      "Gemini",
    "latency_ms":    410,
    "raw_json":      RAW_LLM_TEXT,
    "was_fallback":  False,
}


# ---------------------------------------------------------------------------
# enrich() -- food products
# ---------------------------------------------------------------------------

class TestEnrichFood(unittest.IsolatedAsyncioTestCase):

    async def _run_enrich(self, record, router_response=None, enrichment_id=7):
        """Helper: patch all external dependencies and call enrich()."""
        from core.enricher import enrich

        router_mock = AsyncMock(return_value=router_response or MOCK_ROUTER_RESPONSE)

        with patch("core.enricher.router.analyse",        router_mock), \
             patch("core.enricher.get_or_create_enrichment", return_value=enrichment_id), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            return await enrich(record), router_mock

    async def test_returns_enriched_record(self):
        result, _ = await self._run_enrich(FOOD_RECORD)
        self.assertIsNotNone(result.get("clinical_profile"))
        self.assertEqual(result["clinical_profile"], EXPECTED_CLINICAL_PROFILE)

    async def test_clinical_profile_is_a_dict_not_a_string(self):
        """Regression: pre-fix this assigned the raw flat-text string."""
        result, _ = await self._run_enrich(FOOD_RECORD)
        self.assertIsInstance(result["clinical_profile"], dict)

    async def test_sets_enrichment_llm_model(self):
        result, _ = await self._run_enrich(FOOD_RECORD)
        self.assertEqual(result["_enrichment_llm"], "gemini-2.5-flash")

    async def test_calls_router_with_product_payload(self):
        _, mock = await self._run_enrich(FOOD_RECORD)
        payload = mock.call_args.kwargs["payload"]
        self.assertIn("product", payload)
        self.assertEqual(payload["product"]["gtin"], FOOD_RECORD["gtin"])

    async def test_passes_expected_keys_to_router(self):
        """Regression: enrich() must declare expected_keys so BaseProvider parses."""
        _, mock = await self._run_enrich(FOOD_RECORD)
        kwargs = mock.call_args.kwargs
        self.assertIn("expected_keys", kwargs)
        self.assertIn("fodmap rating", kwargs["expected_keys"])

    async def test_calls_get_or_create_enrichment(self):
        from core.enricher import enrich

        with patch("core.enricher.router.analyse", AsyncMock(return_value=MOCK_ROUTER_RESPONSE)), \
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
        with patch("core.enricher.router.analyse", AsyncMock(return_value=MOCK_ROUTER_RESPONSE)), \
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
        with patch("core.enricher.router.analyse", AsyncMock(return_value=MOCK_ROUTER_RESPONSE)), \
             patch("core.enricher.get_or_create_enrichment", return_value=7), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation", queue_mock), \
             patch("core.enricher._off.save"):
            await enrich(FOOD_RECORD)

        queue_mock.assert_called_once()

    async def test_router_failure_sets_failed_status(self):
        from core.enricher import enrich

        save_mock = AsyncMock()
        with patch("core.enricher.router.analyse", AsyncMock(side_effect=Exception("LLM down"))), \
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
        with patch("core.enricher.router.analyse", AsyncMock(side_effect=Exception("down"))), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation", queue_mock), \
             patch("core.enricher._off.save"):
            await enrich(FOOD_RECORD)

        queue_mock.assert_not_called()

    async def test_empty_parsed_blocks_treated_as_failure(self):
        """If router returns no parsed_blocks, enrichment is treated as failure."""
        from core.enricher import enrich

        empty_response = dict(MOCK_ROUTER_RESPONSE, parsed_blocks=[])
        save_mock = AsyncMock()
        with patch("core.enricher.router.analyse", AsyncMock(return_value=empty_response)), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save", save_mock):
            result = await enrich(FOOD_RECORD)

        # No clinical_profile set, _enrichment_llm gets the model name (no exception was raised)
        # so we end up in the else branch:
        self.assertEqual(result["_enrichment_llm"], "FAILED")


# ---------------------------------------------------------------------------
# enrich() -- non-food products
# ---------------------------------------------------------------------------

class TestEnrichNonFood(unittest.IsolatedAsyncioTestCase):

    async def test_non_food_skips_router(self):
        from core.enricher import enrich

        router_mock = AsyncMock()
        with patch("core.enricher.router.analyse", router_mock), \
             patch("core.enricher.get_or_create_enrichment", return_value=1), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            await enrich(NON_FOOD_RECORD)

        router_mock.assert_not_called()

    async def test_non_food_sets_nop_profile(self):
        from core.enricher import enrich

        with patch("core.enricher.router.analyse", AsyncMock()), \
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

        with patch("core.enricher.router.analyse", AsyncMock()), \
             patch("core.enricher.get_or_create_enrichment", return_value=1), \
             patch("core.enricher.log_scan"), \
             patch("core.enricher._queue_for_validation"), \
             patch("core.enricher._off.save"):
            result = await enrich(NON_FOOD_RECORD)

        self.assertEqual(result["_enrichment_llm"], "NOP")


# ---------------------------------------------------------------------------
# _coerce_clinical() -- type coercion
# ---------------------------------------------------------------------------

class TestCoerceClinical(unittest.TestCase):

    def test_normalises_keys_to_snake_case(self):
        from core.enricher import _coerce_clinical
        out = _coerce_clinical(PARSED_BLOCK)
        self.assertEqual(set(out.keys()), set(EXPECTED_CLINICAL_PROFILE.keys()))

    def test_ratings_coerced_to_int(self):
        from core.enricher import _coerce_clinical
        out = _coerce_clinical(PARSED_BLOCK)
        self.assertIsInstance(out["fodmap_rating"], int)
        self.assertEqual(out["coeliac_rating"], 3)

    def test_invalid_rating_falls_back_to_minus_one(self):
        from core.enricher import _coerce_clinical
        block = dict(PARSED_BLOCK, **{"fodmap rating": "garbage"})
        self.assertEqual(_coerce_clinical(block)["fodmap_rating"], -1)

    def test_health_star_coerced_to_float(self):
        from core.enricher import _coerce_clinical
        out = _coerce_clinical(PARSED_BLOCK)
        self.assertIsInstance(out["estimated_health_star"], float)

    def test_health_star_null_string_becomes_none(self):
        from core.enricher import _coerce_clinical
        block = dict(PARSED_BLOCK, **{"estimated health star": "null"})
        self.assertIsNone(_coerce_clinical(block)["estimated_health_star"])

    def test_allergens_split_to_list(self):
        from core.enricher import _coerce_clinical
        block = dict(PARSED_BLOCK, allergens="Milk, Soy, Wheat")
        self.assertEqual(_coerce_clinical(block)["allergen_warnings"],
                         ["Milk", "Soy", "Wheat"])

    def test_empty_allergens_becomes_empty_list(self):
        from core.enricher import _coerce_clinical
        block = dict(PARSED_BLOCK, allergens="")
        self.assertEqual(_coerce_clinical(block)["allergen_warnings"], [])


# ---------------------------------------------------------------------------
# _queue_for_validation_sync() -- isolated
# ---------------------------------------------------------------------------

class TestQueueForValidation(unittest.TestCase):

    def test_writes_json_line(self):
        from core.enricher import _queue_for_validation_sync

        written = []
        m = mock_open()
        m.return_value.__enter__.return_value.write = written.append

        with patch("builtins.open", m), \
             patch("os.makedirs"):
            _queue_for_validation_sync(FOOD_RECORD)

        self.assertTrue(any(FOOD_RECORD["gtin"] in line for line in written))

    def test_silently_handles_write_error(self):
        """File write failures must not propagate -- enrichment should continue."""
        from core.enricher import _queue_for_validation_sync

        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch("os.makedirs"):
            _queue_for_validation_sync(FOOD_RECORD)  # must not raise


if __name__ == "__main__":
    unittest.main()
