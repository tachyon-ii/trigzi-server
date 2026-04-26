"""
=============================================================================
Module:        Test — GTIN Normalisation
Location:      tests/test_gtin.py
Description:   Tests for utils/gtin.py — GTIN normalisation following the
               Open Food Facts specification. Verifies every barcode
               variant the scanners can emit (EAN-8, UPC, EAN-13,
               EAN-14, malformed/empty inputs) is coerced to the right
               canonical 13-digit form or rejected with None.

Architecture Note:
GTIN normalisation is on the hot path of every product scan; if it
silently mishandles a length the lookups miss and the user sees
"product not found" for items genuinely in the database. Each branch
of the algorithm gets at least one direct test, plus a handful of
edge cases (whitespace, non-numeric, EAN-14 with non-zero leader).
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring (TestNormalise)
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
#   duplicate-code         — sys.path bootstrap try/except pattern is shared across
#                            tests that need to import from project-relative paths;
#                            extracting it would create a tests/ helper module
#                            that would itself need a bootstrap. Accepting the dup.
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument,duplicate-code

from __future__ import annotations

import os
import sys

# sys.path bootstrap so this file works whether pytest runs from the project
# root or directly. The try/except wrapping declares to pylint that the
# project import after the path mutation is intentional.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from utils.gtin import normalise, variations  # pylint: disable=ungrouped-imports
except ImportError as exc:
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)


class TestNormalise:

    # --- Valid EAN-13 ---

    def test_ean13_already_canonical(self):
        assert normalise('0070177161170') == '0070177161170'

    def test_ean13_no_leading_zeros(self):
        assert normalise('0034000470693') == '0034000470693'

    # --- Padding to 13 ---

    def test_11_digits_padded_to_13(self):
        assert normalise('70177161170') == '0070177161170'

    def test_10_digits_padded_to_13(self):
        assert normalise('7017716117') == '0007017716117'

    def test_8_digits_padded_to_13(self):
        assert normalise('50819461') == '0000050819461'

    def test_8_zeros_padded_to_13(self):
        assert normalise('00000000') == '0000000000000'

    def test_7_digits_padded_to_13(self):
        assert normalise('1234567') == '0000001234567'

    def test_1_digit_padded_to_13(self):
        assert normalise('1') == '0000000000001'

    # --- EAN-14 handling ---

    def test_ean14_leading_zero_trimmed_to_13(self):
        assert normalise('00340004706930') == '0340004706930'

    def test_ean14_nonzero_leader_dropped(self):
        assert normalise('10034000470693') is None

    # --- Invalid inputs ---

    def test_over_14_digits_dropped(self):
        assert normalise('00001234567890123') is None

    def test_non_numeric_dropped(self):
        assert normalise('abc') is None

    def test_alphanumeric_dropped(self):
        assert normalise('123abc456') is None

    def test_empty_string_dropped(self):
        assert normalise('') is None

    def test_none_dropped(self):
        assert normalise(None) is None

    def test_whitespace_stripped(self):
        assert normalise('  0070177161170  ') == '0070177161170'


class TestVariations:

    def test_valid_gtin_returns_one_candidate(self):
        assert variations('0070177161170') == ['0070177161170']

    def test_short_gtin_returns_padded(self):
        assert variations('70177161170') == ['0070177161170']

    def test_invalid_returns_empty(self):
        assert not variations('abc')

    def test_empty_returns_empty(self):
        assert not variations('')

    def test_ean14_leading_zero_returns_ean13(self):
        assert variations('00340004706930') == ['0340004706930']

    def test_ean14_nonzero_returns_empty(self):
        assert not variations('10034000470693')
