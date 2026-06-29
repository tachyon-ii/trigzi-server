"""
=============================================================================
Module:        Test — Menu Analysis Pipeline
Location:      tests/test_menu.py
Description:   Tests for analyse_menu — the OCR menu parsing pipeline.

               Section 1: Fixture sanity
               Section 2: Mocked unit tests — response shape, parsing
               Section 3: Integration tests — real LLM, ingredient quality
               Section 4: Edge cases — non-menu, empty, numeric-only input

               Sections 1 and 2 run without LLM calls (fast, CI-safe).
               Sections 3 and 4 hit the real LLM and are slower.

Run all:
    python -m pytest tests/test_menu.py -v --asyncio-mode=auto

Run fast (unit only):
    python -m pytest tests/test_menu.py -v -k "not Integration and not Edge"
=============================================================================
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test class names and method names ARE the docstrings for this module —
# enforcing prose docstrings on every test method produces noise with no
# signal. The module-level docstring above covers purpose and structure.

from __future__ import annotations

import os
import re
import sys
import logging
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.analyser import analyse_menu  # hoisted: eliminates import-outside-toplevel

logging.disable(logging.CRITICAL)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "valid_menu.txt"

SEZAR_MENU = FIXTURE_PATH.read_text().strip()

# Minimal mock LLM response — valid shape, used for unit tests
MOCK_MENU_RESPONSE = {
    "result": (
        "Valid_Input: true\n"
        "Dish: OYSTERS\n"
        "Listed: apple, aniseed, sumac\n"
        "Suspected: lemon, mignonette\n"
        "---\n"
        "Dish: HOMMUS\n"
        "Listed: chickpeas, brown butter, toasted lavash\n"
        "Suspected: tahini, garlic, olive oil\n"
        "---"
    ),
    "model":        "gemini-2.5-flash",
    "provider":     "Gemini",
    "latency_ms":   320,
    "was_fallback": False,
}

MOCK_INVALID_RESPONSE = {
    "result":       "Valid_Input: false\n---",
    "model":        "gemini-2.5-flash",
    "provider":     "Gemini",
    "latency_ms":   120,
    "was_fallback": False,
}


# ---------------------------------------------------------------------------
# Section 1: Fixture sanity
# ---------------------------------------------------------------------------

class TestFixture(unittest.TestCase):
    """Verify the test fixture file is present and non-trivially sized."""

    def test_fixture_file_exists(self):
        self.assertTrue(FIXTURE_PATH.exists(), f"Fixture missing: {FIXTURE_PATH}")

    def test_fixture_file_not_empty(self):
        content = FIXTURE_PATH.read_text().strip()
        self.assertGreater(len(content), 50)


# ---------------------------------------------------------------------------
# Section 2: Mocked unit tests — shape and parsing (no real LLM)
# ---------------------------------------------------------------------------

class TestAnalyseMenuUnit(unittest.IsolatedAsyncioTestCase):
    """Unit tests for analyse_menu — all LLM calls mocked, fast and CI-safe."""

    async def test_returns_dict_on_valid_response(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)

    async def test_result_has_type_field(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        self.assertIn("type", result)

    async def test_result_type_is_menu(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        self.assertEqual(result["type"], "menu")

    async def test_result_has_items_list(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        self.assertIn("items", result)
        self.assertIsInstance(result["items"], list)

    async def test_items_not_empty(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        self.assertGreater(len(result["items"]), 0)

    async def test_each_item_has_name(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        for item in result["items"]:
            self.assertIn("name", item)
            self.assertTrue(item["name"].strip())

    async def test_each_item_has_listed_ingredients(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        for item in result["items"]:
            self.assertIn("listed_ingredients", item)
            self.assertIsInstance(item["listed_ingredients"], list)

    async def test_each_item_has_suspected_ingredients(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_MENU_RESPONSE
            result = await analyse_menu(text=SEZAR_MENU)
        for item in result["items"]:
            self.assertIn("suspected_ingredients", item)
            self.assertIsInstance(item["suspected_ingredients"], list)

    async def test_returns_none_on_invalid_input(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.return_value = MOCK_INVALID_RESPONSE
            result = await analyse_menu(text="not a menu")
        self.assertIsNone(result)

    # pylint: disable=duplicate-code
    # This early-exit + assert_not_called pattern is necessarily identical to
    # the equivalent tests in test_analyser.py — both verify the same contract
    # on the same function signature. A shared helper would couple the two
    # modules without improving clarity.
    async def test_returns_none_on_empty_text(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            result = await analyse_menu(text="")
        self.assertIsNone(result)
        mock.assert_not_called()

    async def test_returns_none_on_router_exception(self):
        with patch("core.analyser.router.analyse", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("Router failed")
            result = await analyse_menu(text=SEZAR_MENU)
        self.assertIsNone(result)
    # pylint: enable=duplicate-code


# ---------------------------------------------------------------------------
# Section 3: Integration tests — real LLM, ingredient quality
# These encode the prompt behaviours we require.
# Failures here mean the prompt needs fixing, not the test.
# ---------------------------------------------------------------------------

class TestAnalyseMenuIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests against the real LLM — slower, not CI-safe."""

    async def asyncSetUp(self):
        """Run analyse_menu once; share result across all tests in this class."""
        self.result = await analyse_menu(text=SEZAR_MENU)
        self.assertIsNotNone(self.result, "analyse_menu returned None — LLM or parse failure")
        self.items = self.result["items"]

    def _find(self, fragment):
        for item in self.items:
            if fragment.lower() in item["name"].lower():
                return item
        return None

    def _all_ingredients(self, item):
        return item["listed_ingredients"] + item["suspected_ingredients"]

    def test_minimum_dish_count(self):
        """Sezar fixture has 22 dishes — expect at least 15."""
        self.assertGreaterEqual(
            len(self.items), 15,
            f"Expected >=15 dishes, got {len(self.items)}"
        )

    def test_all_items_have_name(self):
        for item in self.items:
            self.assertTrue(item.get("name", "").strip(), f"Blank name: {item}")

    def test_all_items_have_listed_ingredients(self):
        for item in self.items:
            self.assertIsInstance(item.get("listed_ingredients"), list)

    def test_all_items_have_suspected_ingredients(self):
        for item in self.items:
            self.assertIsInstance(item.get("suspected_ingredients"), list)

    def test_ampersand_expanded_oysters(self):
        """'aniseed & sumac' should become separate ingredients."""
        oysters = self._find("oyster")
        self.assertIsNotNone(oysters, "OYSTERS not found")
        all_ing = [i.lower() for i in self._all_ingredients(oysters)]
        combined = " ".join(all_ing)
        self.assertNotIn(" & ", combined,
            f"Ampersand not expanded in OYSTERS: {all_ing}")
        self.assertTrue(any("aniseed" in i for i in all_ing),
            f"aniseed missing from OYSTERS: {all_ing}")
        self.assertTrue(any("sumac" in i for i in all_ing),
            f"sumac missing from OYSTERS: {all_ing}")

    def test_quantity_annotations_stripped(self):
        """(2pcs), (4pcs), (2pc) must not appear in any ingredient."""
        qty_pattern = re.compile(r'\(\d+\s*pc[s]?\)', re.IGNORECASE)
        for item in self.items:
            for ing in self._all_ingredients(item):
                self.assertIsNone(
                    qty_pattern.search(ing),
                    f"Quantity annotation not stripped: '{ing}' in '{item['name']}'"
                )

    def test_dish_name_not_in_suspected(self):
        """KATAIFI WRAPPED LAMB — 'lamb' must not appear in suspected."""
        lamb = self._find("kataifi")
        self.assertIsNotNone(lamb, "KATAIFI WRAPPED LAMB not found")
        suspected_lower = [i.lower() for i in lamb["suspected_ingredients"]]
        self.assertNotIn("lamb", suspected_lower,
            f"'lamb' appears in suspected: {suspected_lower}")

    def test_cooking_methods_stripped(self):
        """Generic cooking methods must not prefix base proteins.

        'preserved lemon', 'roasted grapes', 'whipped tahini' are legitimate
        ingredient names — the adjective is inseparable from the ingredient.
        We only flag method+protein combinations where the method adds no
        information beyond how the protein was cooked.
        """
        base_proteins = ["chicken", "lamb", "beef", "fish", "crab", "pork", "salmon"]
        methods = ["grilled", "braised", "slow-roasted", "smoked", "fried",
                   "baked", "steamed", "poached", "charred"]
        for item in self.items:
            for ing in self._all_ingredients(item):
                ing_lower = ing.lower().strip()
                for method in methods:
                    for protein in base_proteins:
                        self.assertFalse(
                            ing_lower == f"{method} {protein}",
                            f"Method+protein not stripped: '{ing}' in '{item['name']}'"
                        )

    def test_listed_ingredients_are_atomic(self):
        """No listed ingredient should contain ' & '."""
        for item in self.items:
            for ing in item["listed_ingredients"]:
                self.assertNotIn(" & ", ing,
                    f"Compound not split: '{ing}' in '{item['name']}'")

    def test_suspected_ingredients_not_universally_empty(self):
        """Most dishes should have at least one suspected ingredient."""
        empty_count = sum(
            1 for item in self.items if not item["suspected_ingredients"]
        )
        total = len(self.items)
        self.assertLess(empty_count, total // 2,
            f"{empty_count}/{total} dishes have empty suspected — prompt may be broken")

    def test_dish_name_ingredient_in_listed(self):
        """Ingredients implied by the dish name must appear in listed.

        OYSTERS | apple, aniseed & sumac -> listed must include 'oysters'.
        KATAIFI WRAPPED LAMB | sesame aioli -> listed must include 'lamb'.
        Rule 6 in the prompt: dish name implies the primary ingredient.
        """
        cases = {
            "oyster":   "oysters",
            "kataifi":  "lamb",
            "kingfish": "kingfish",
            "chicken":  "chicken",
        }
        for fragment, expected in cases.items():
            item = self._find(fragment)
            if item is None:
                continue
            listed_lower = [i.lower() for i in item["listed_ingredients"]]
            self.assertTrue(
                any(expected in i for i in listed_lower),
                f"Expected '{expected}' in listed for '{item['name']}': {listed_lower}"
            )


# ---------------------------------------------------------------------------
# Section 4: Edge cases — real LLM
# ---------------------------------------------------------------------------

class TestAnalyseMenuEdgeCases(unittest.IsolatedAsyncioTestCase):
    """Edge-case inputs that must return None — real LLM, slower."""

    async def test_non_menu_text_returns_none(self):
        result = await analyse_menu(
            text="The quick brown fox jumps over the lazy dog."
        )
        self.assertIsNone(result)

    async def test_empty_text_returns_none(self):
        result = await analyse_menu(text="")
        self.assertIsNone(result)

    async def test_numeric_only_returns_none(self):
        result = await analyse_menu(text="42 18 17 18 16 27 24 30 25 27")
        self.assertIsNone(result)

    async def test_novel_text_returns_none(self):
        result = await analyse_menu(
            text=(
                "Call me Ishmael. Some years ago never mind how long precisely "
                "having little or no money in my purse and nothing particular "
                "to interest me on shore I thought I would sail about a little "
                "and see the watery part of the world."
            )
        )
        self.assertIsNone(result)
