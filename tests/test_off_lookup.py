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
from unittest.mock import AsyncMock, MagicMock, patch

from utils.off_lookup import OFFLookup

SAMPLE_RECORD = {
    "gtin": "0000000000000",
    "name": "Test Product",
    "source": "off"
}

def _mock_db(fetch_return=None, execute_side_effect=None):
    """
    Creates a deep mock of the aiomysql pool -> conn -> cursor hierarchy.
    Strictly models the synchronous methods returning asynchronous context managers.
    """
    # 1. The cursor itself (Async operations: execute, fetchone)
    mock_cur = AsyncMock()
    if execute_side_effect:
        mock_cur.execute.side_effect = execute_side_effect
    mock_cur.fetchone.return_value = fetch_return

    # 2. The cursor context manager (Sync call returning Async CM)
    mock_cursor_ctx = AsyncMock()
    mock_cursor_ctx.__aenter__.return_value = mock_cur

    # 3. The connection (Sync operations: cursor)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_ctx

    # 4. The pool context manager (Sync call returning Async CM)
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn

    # 5. The pool (Sync operations: acquire)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire_ctx

    return mock_pool, mock_cur


class BaseDBTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lookup = OFFLookup()
        # Mute the raw print() statements in the module during testing
        self.print_patcher = patch('builtins.print')
        self.print_patcher.start()

    def tearDown(self):
        self.print_patcher.stop()


class TestOFFLookupGet(BaseDBTest):
    async def test_returns_record_when_found(self):
        mock_pool, _ = _mock_db({"data": json.dumps(SAMPLE_RECORD)})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            result = await self.lookup.get("123")
            self.assertEqual(result["name"], "Test Product")

    async def test_returns_none_when_not_found(self):
        mock_pool, _ = _mock_db(None)
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            result = await self.lookup.get("123")
            self.assertIsNone(result)

    async def test_normalises_gtin_before_query(self):
        """Short GTINs should be padded to 13 digits before lookup."""
        mock_pool, mock_cur = _mock_db({"data": json.dumps(SAMPLE_RECORD)})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            await self.lookup.get("123")
            # Verify the padded GTIN was passed to the execute tuple
            called_args = mock_cur.execute.call_args[0][1]
            self.assertEqual(called_args[0], "0000000000123")

    async def test_returns_none_for_invalid_gtin(self):
        # Empty string results in early exit (no DB call)
        with patch("utils.off_lookup.get_pool") as mock_get_pool:
            result = await self.lookup.get("")
            self.assertIsNone(result)
            mock_get_pool.assert_not_called()

    async def test_deserialises_json_string(self):
        mock_pool, _ = _mock_db({"data": json.dumps(SAMPLE_RECORD)})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            result = await self.lookup.get("123")
            self.assertIsInstance(result, dict)

    async def test_returns_dict_when_data_already_dict(self):
        mock_pool, _ = _mock_db({"data": SAMPLE_RECORD})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            result = await self.lookup.get("123")
            self.assertIsInstance(result, dict)

    async def test_returns_none_on_db_exception(self):
        mock_pool, _ = _mock_db(execute_side_effect=Exception("DB Down"))
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            result = await self.lookup.get("123")
            self.assertIsNone(result)


class TestOFFLookupExists(BaseDBTest):
    async def test_returns_true_when_row_found(self):
        mock_pool, _ = _mock_db({"1": 1})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            self.assertTrue(await self.lookup.exists("123"))

    async def test_returns_false_when_row_missing(self):
        mock_pool, _ = _mock_db(None)
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            self.assertFalse(await self.lookup.exists("123"))

    async def test_returns_false_for_invalid_gtin(self):
        with patch("utils.off_lookup.get_pool") as mock_get_pool:
            self.assertFalse(await self.lookup.exists(""))
            mock_get_pool.assert_not_called()


class TestOFFLookupIsEnriched(BaseDBTest):
    async def test_returns_true_when_enrichment_id_set(self):
        mock_pool, _ = _mock_db({"enrichment_id": 42})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            self.assertTrue(await self.lookup.is_enriched("123"))

    async def test_returns_false_when_enrichment_id_null(self):
        mock_pool, _ = _mock_db({"enrichment_id": None})
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            self.assertFalse(await self.lookup.is_enriched("123"))

    async def test_returns_false_when_row_missing(self):
        mock_pool, _ = _mock_db(None)
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            self.assertFalse(await self.lookup.is_enriched("123"))


class TestOFFLookupSave(BaseDBTest):
    async def test_save_upserts_record(self):
        mock_pool, mock_cur = _mock_db()
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            success = await self.lookup.save(SAMPLE_RECORD)
            self.assertTrue(success)
            self.assertTrue(mock_cur.execute.called)

    async def test_save_passes_enrichment_id(self):
        mock_pool, mock_cur = _mock_db()
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            await self.lookup.save(SAMPLE_RECORD, enrichment_id=99)
            called_args = mock_cur.execute.call_args[0][1]
            self.assertEqual(called_args[3], 99)

    async def test_save_none_enrichment_id_allowed(self):
        mock_pool, mock_cur = _mock_db()
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            await self.lookup.save(SAMPLE_RECORD, enrichment_id=None)
            called_args = mock_cur.execute.call_args[0][1]
            self.assertIsNone(called_args[3])

    async def test_save_normalises_gtin(self):
        mock_pool, mock_cur = _mock_db()
        record = dict(SAMPLE_RECORD, gtin="123")
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            await self.lookup.save(record)
            called_args = mock_cur.execute.call_args[0][1]
            self.assertEqual(called_args[0], "0000000000123")

    async def test_save_returns_false_for_invalid_gtin(self):
        with patch("utils.off_lookup.get_pool") as mock_get_pool:
            record = dict(SAMPLE_RECORD, gtin="")
            success = await self.lookup.save(record)
            self.assertFalse(success)
            mock_get_pool.assert_not_called()

    async def test_save_returns_false_on_db_exception(self):
        mock_pool, _ = _mock_db(execute_side_effect=Exception("DB Down"))
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            success = await self.lookup.save(SAMPLE_RECORD)
            self.assertFalse(success)

    async def test_save_truncates_name_at_150(self):
        mock_pool, mock_cur = _mock_db()
        record = dict(SAMPLE_RECORD, name="X" * 200)
        with patch("utils.off_lookup.get_pool", return_value=mock_pool):
            await self.lookup.save(record)
            called_args = mock_cur.execute.call_args[0][1]
            # Ensure name parameter was truncated
            self.assertEqual(len(called_args[2]), 150)

if __name__ == "__main__":
    unittest.main(verbosity=2)
