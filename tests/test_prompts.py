"""
=============================================================================
Module:        Test — Prompt Schema Contracts
Location:      tests/test_prompts.py
Description:   Validates every prompt template in prompts/ against the
               strict [OUTPUT] contract enforced by SchemaValidator.
               Each prompt must declare its expected response shape so
               the router/analyser layer can rely on field presence
               and type when extracting JSON from LLM responses.

Architecture Note:
Uses subTest so a single malformed prompt doesn't halt validation of
the rest — failures surface as a per-prompt list rather than first-
failure-wins. The validator catches contract drift early, before a
schema mismatch reaches the analyser at runtime and produces an
opaque KeyError or wrong-type bug.
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestPromptsContract)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
#   duplicate-code         — sys.path bootstrap try/except pattern is shared across
#                            tests that need to import from project-relative paths;
#                            extracting it would create a tests/ helper module
#                            that would itself need a bootstrap. Accepting the dup.
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument,duplicate-code

import glob
import os
import sys
import unittest

# sys.path bootstrap so this file works whether pytest runs from the project root
# or directly. The try/except wrapping declares to pylint that the import after
# the path mutation is intentional.
try:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from core.llm.validator import SchemaValidator  # pylint: disable=ungrouped-imports
except ImportError as exc:
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)


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
