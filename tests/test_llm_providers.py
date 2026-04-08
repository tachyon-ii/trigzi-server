#!/usr/bin/env python3
#
# tests/test_llm_providers.py
#
# Validates that core/llm/llm_providers.json is strictly valid JSON
# and conforms to the required application schema.
#

import os
import json
import unittest

class TestLLMProvidersJSON(unittest.TestCase):
    def setUp(self):
        self.filepath = os.path.join(
            os.path.dirname(__file__), '..', 'core', 'llm', 'llm_providers.json'
        )

    def test_01_file_exists(self):
        """Ensure the llm_providers.json file exists."""
        self.assertTrue(os.path.exists(self.filepath), f"File not found: {self.filepath}")

    def test_02_valid_json_syntax(self):
        """Ensure the file contains valid JSON (no trailing commas, missing quotes, etc.)."""
        with open(self.filepath, 'r', encoding='utf-8') as f:
            try:
                json.load(f)
            except json.JSONDecodeError as e:
                self.fail(f"Invalid JSON syntax in llm_providers.json (Check for trailing commas!): {e}")

    def test_03_required_top_level_structure(self):
        """Ensure the top-level structure contains 'providers' and 'routing'."""
        with open(self.filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.assertIn("providers", data, "Missing 'providers' key at the top level.")
        self.assertIn("routing", data, "Missing 'routing' key at the top level.")
        self.assertIsInstance(data["providers"], dict)
        self.assertIsInstance(data["routing"], dict)

    def test_04_routing_schema(self):
        """Ensure every routing block has models, optimize, and timeout."""
        with open(self.filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for task, config in data["routing"].items():
            self.assertIn("models", config, f"Task '{task}' is missing 'models' array.")
            self.assertIsInstance(config["models"], list, f"Task '{task}' 'models' must be a list.")
            self.assertTrue(len(config["models"]) > 0, f"Task '{task}' 'models' list cannot be empty.")
            
            self.assertIn("optimize", config, f"Task '{task}' is missing 'optimize'.")
            self.assertIn("timeout", config, f"Task '{task}' is missing 'timeout'.")

    def test_05_provider_schema(self):
        """Ensure every provider block has a defaultModel and hierarchy."""
        with open(self.filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        for provider, config in data["providers"].items():
            self.assertIn("defaultModel", config, f"Provider '{provider}' missing 'defaultModel'.")
            self.assertIn("hierarchy", config, f"Provider '{provider}' missing 'hierarchy'.")
            self.assertIsInstance(config["hierarchy"], list)

if __name__ == '__main__':
    unittest.main(verbosity=2)
