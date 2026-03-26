#!/usr/bin/env python3
from __future__ import annotations
"""
tests/test_gtin.py

Tests for utils/gtin.py — GTIN normalisation following OFF specification.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from utils.gtin import normalise, variations


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
        assert variations('abc') == []

    def test_empty_returns_empty(self):
        assert variations('') == []

    def test_ean14_leading_zero_returns_ean13(self):
        assert variations('00340004706930') == ['0340004706930']

    def test_ean14_nonzero_returns_empty(self):
        assert variations('10034000470693') == []
