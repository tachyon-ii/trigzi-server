#!/usr/bin/env python3
#
# tests/test_prompts.py
#
# Validates all prompt templates against the strict [OUTPUT] contract
# defined in core/llm/validator.py.
#

import os
import glob
import sys
import unittest

# Add project root to path so we can import core
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.llm.validator import SchemaValidator

class TestPromptsContract(unittest.TestCase):
    
    def test_all_prompts_match_schema(self):
        """Ensure every .txt file in the prompts directory passes the SchemaValidator."""
        prompts_dir = os.path.join(os.path.dirname(__file__), '..', 'prompts')
        prompt_files = glob.glob(os.path.join(prompts_dir, '*.txt'))

        self.assertTrue(len(prompt_files) > 0, "No prompt .txt files found to test.")

        for filepath in prompt_files:
            filename = os.path.basename(filepath)
            
            # 🛡️ Using subTest ensures that one failing prompt doesn't stop the test 
            # from checking the rest of the files in the directory.
            with self.subTest(prompt=filename):
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()

                try:
                    SchemaValidator.validate_prompt_contract(content)
                except ValueError as e:
                    self.fail(f"Prompt '{filename}' failed validation:\n{e}")

if __name__ == '__main__':
    unittest.main(verbosity=2)
