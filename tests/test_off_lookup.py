#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_off_lookup.py
#
#  Unit tests for utils/off_lookup.py
#  All DB calls are mocked -- no live database required.
#

import json
import unittest
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.off_lookup import OFFLookup


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_conn(fetchone_return=None):
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__  = MagicMock(return_value=False)
    cur.fetchone  = MagicMock(return_value=fetchone_return)

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor    = MagicMock(return_value=cur)
    return conn, cur


SAMPLE_RECORD = {
    "gtin":    "0070177161170",
    "source":  "off",
    "name":    "Test Product",
    "brand":   "Test Brand",
    "nutrition_100g": {"energy_kj": 400.0},
    "clinical_profile": None,
    "_enrichment_llm": None,
}


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

class TestOFFLookupGet(unittest.TestCase):

    def test_returns_record_when_found(self):
        conn, cur = _make_conn({"data": json.dumps(SAMPLE_RECORD)})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().get("0070177161170")
        self.assertIsNotNone(result)
        self.assertEqual(result["gtin"], "0070177161170")
        self.assertEqual(result["name"], "Test Product")

    def test_returns_none_when_not_found(self):
        conn, cur = _make_conn(None)
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().get("0070177161170")
        self.assertIsNone(result)

    def test_normalises_gtin_before_query(self):
        """Short GTINs should be padded to 13 digits before lookup."""
        conn, cur = _make_conn({"data": json.dumps(SAMPLE_RECORD)})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            OFFLookup().get("70177161170")  # 11 digits
        # args[0] = SQL string, args[1] = params tuple
        sql, params = cur.execute.call_args[0]
        self.assertIn("0070177161170", params)

    def test_returns_none_for_invalid_gtin(self):
        with patch("utils.off_lookup.get_conn") as mock_conn:
            result = OFFLookup().get("abc")
        self.assertIsNone(result)
        mock_conn.assert_not_called()

    def test_deserialises_json_string(self):
        conn, cur = _make_conn({"data": json.dumps(SAMPLE_RECORD)})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().get("0070177161170")
        self.assertIsInstance(result, dict)

    def test_returns_dict_when_data_already_dict(self):
        conn, cur = _make_conn({"data": SAMPLE_RECORD})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().get("0070177161170")
        self.assertEqual(result["name"], "Test Product")

    def test_returns_none_on_db_exception(self):
        conn, cur = _make_conn()
        cur.execute.side_effect = Exception("DB down")
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().get("0070177161170")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# exists()
# ---------------------------------------------------------------------------

class TestOFFLookupExists(unittest.TestCase):

    def test_returns_true_when_row_found(self):
        conn, cur = _make_conn({"1": 1})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            self.assertTrue(OFFLookup().exists("0070177161170"))

    def test_returns_false_when_row_missing(self):
        conn, cur = _make_conn(None)
        with patch("utils.off_lookup.get_conn", return_value=conn):
            self.assertFalse(OFFLookup().exists("0070177161170"))

    def test_returns_false_for_invalid_gtin(self):
        with patch("utils.off_lookup.get_conn") as mock_conn:
            self.assertFalse(OFFLookup().exists(""))
        mock_conn.assert_not_called()


# ---------------------------------------------------------------------------
# is_enriched()
# ---------------------------------------------------------------------------

class TestOFFLookupIsEnriched(unittest.TestCase):

    def test_returns_true_when_enrichment_id_set(self):
        conn, cur = _make_conn({"enrichment_id": 42})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            self.assertTrue(OFFLookup().is_enriched("0070177161170"))

    def test_returns_false_when_enrichment_id_null(self):
        conn, cur = _make_conn({"enrichment_id": None})
        with patch("utils.off_lookup.get_conn", return_value=conn):
            self.assertFalse(OFFLookup().is_enriched("0070177161170"))

    def test_returns_false_when_row_missing(self):
        conn, cur = _make_conn(None)
        with patch("utils.off_lookup.get_conn", return_value=conn):
            self.assertFalse(OFFLookup().is_enriched("0070177161170"))


# ---------------------------------------------------------------------------
# save()
# ---------------------------------------------------------------------------

class TestOFFLookupSave(unittest.TestCase):

    def test_save_upserts_record(self):
        conn, cur = _make_conn()
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().save(SAMPLE_RECORD, enrichment_id=7)
        self.assertTrue(result)
        sql, params = cur.execute.call_args[0]
        self.assertIn("INSERT INTO products", sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)

    def test_save_passes_enrichment_id(self):
        conn, cur = _make_conn()
        with patch("utils.off_lookup.get_conn", return_value=conn):
            OFFLookup().save(SAMPLE_RECORD, enrichment_id=99)
        _, params = cur.execute.call_args[0]
        self.assertIn(99, params)

    def test_save_none_enrichment_id_allowed(self):
        conn, cur = _make_conn()
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().save(SAMPLE_RECORD, enrichment_id=None)
        self.assertTrue(result)
        _, params = cur.execute.call_args[0]
        self.assertIn(None, params)

    def test_save_normalises_gtin(self):
        conn, cur = _make_conn()
        record = dict(SAMPLE_RECORD, gtin="70177161170")
        with patch("utils.off_lookup.get_conn", return_value=conn):
            OFFLookup().save(record)
        _, params = cur.execute.call_args[0]
        self.assertEqual(params[0], "0070177161170")

    def test_save_returns_false_for_invalid_gtin(self):
        with patch("utils.off_lookup.get_conn") as mock_conn:
            result = OFFLookup().save({"gtin": "abc"})
        self.assertFalse(result)
        mock_conn.assert_not_called()

    def test_save_returns_false_on_db_exception(self):
        conn, cur = _make_conn()
        cur.execute.side_effect = Exception("DB error")
        with patch("utils.off_lookup.get_conn", return_value=conn):
            result = OFFLookup().save(SAMPLE_RECORD)
        self.assertFalse(result)

    def test_save_truncates_name_at_150(self):
        conn, cur = _make_conn()
        record = dict(SAMPLE_RECORD, name="X" * 200)
        with patch("utils.off_lookup.get_conn", return_value=conn):
            OFFLookup().save(record)
        _, params = cur.execute.call_args[0]
        self.assertLessEqual(len(params[2]), 150)


if __name__ == "__main__":
    unittest.main()
