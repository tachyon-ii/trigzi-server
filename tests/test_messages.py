#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_messages.py
#
#  Unit tests for core/messages/messages_service.py
#  No network calls, no DB, no server required.
#

import unittest
from unittest.mock import patch, AsyncMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.messages.messages_service import (
    get_messages,
    _pick_daily,
    _eligible,
    _date_ordinal,
)

DEVICE_A = "550e8400-e29b-41d4-a716-446655440000"
DEVICE_B = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


class TestPickDaily(unittest.TestCase):
    """_pick_daily — determinism, rotation, device variance."""

    def test_returns_none_for_empty_pool(self):
        self.assertIsNone(_pick_daily([], DEVICE_A))

    def test_returns_single_item_always(self):
        pool = [{"id": "motd-001"}]
        self.assertEqual(_pick_daily(pool, DEVICE_A), pool[0])

    def test_same_device_same_day_same_result(self):
        pool = [{"id": f"motd-{i:03d}"} for i in range(40)]
        result_1 = _pick_daily(pool, DEVICE_A)
        result_2 = _pick_daily(pool, DEVICE_A)
        self.assertEqual(result_1, result_2)

    def test_different_devices_may_differ(self):
        """With 40 quotes the two devices should land on different ones."""
        pool = [{"id": f"motd-{i:03d}"} for i in range(40)]
        a = _pick_daily(pool, DEVICE_A)
        b = _pick_daily(pool, DEVICE_B)
        # Not guaranteed to differ for every pool size, but with 40 entries
        # and two distinct UUIDs the probability of collision is ~2.5%.
        # If this ever flakes, increase pool size.
        self.assertNotEqual(a, b)

    def test_rotates_on_next_day(self):
        pool = [{"id": f"motd-{i:03d}"} for i in range(40)]
        today_ordinal    = _date_ordinal()
        tomorrow_ordinal = today_ordinal + 1

        with patch("core.messages.messages_service._date_ordinal", return_value=today_ordinal):
            today = _pick_daily(pool, DEVICE_A)
        with patch("core.messages.messages_service._date_ordinal", return_value=tomorrow_ordinal):
            tomorrow = _pick_daily(pool, DEVICE_A)

        self.assertNotEqual(today, tomorrow)

    def test_index_stays_within_pool_bounds(self):
        """seed % len(pool) must never raise IndexError regardless of seed size."""
        pool = [{"id": f"motd-{i:03d}"} for i in range(40)]
        for device in [DEVICE_A, DEVICE_B, "x" * 64, "a"]:
            result = _pick_daily(pool, device)
            self.assertIn(result, pool)


class TestEligible(unittest.TestCase):
    """_eligible — day-tag and context filtering."""

    def test_untagged_always_eligible(self):
        msg = {"id": "motd-001", "context": "motd"}
        self.assertTrue(_eligible(msg, "tuesday", None))

    def test_tagged_eligible_on_matching_day(self):
        msg = {"id": "motd-038", "context": "motd", "tags": ["monday"]}
        self.assertTrue(_eligible(msg, "monday", None))

    def test_tagged_ineligible_on_other_day(self):
        msg = {"id": "motd-038", "context": "motd", "tags": ["monday"]}
        self.assertFalse(_eligible(msg, "wednesday", None))

    def test_context_filter_match(self):
        msg = {"id": "motd-001", "context": "motd"}
        self.assertTrue(_eligible(msg, "monday", "motd"))

    def test_context_filter_no_match(self):
        msg = {"id": "motd-001", "context": "motd"}
        self.assertFalse(_eligible(msg, "monday", "alert"))

    def test_no_context_filter_passes_all(self):
        msg = {"id": "motd-001", "context": "motd"}
        self.assertTrue(_eligible(msg, "monday", None))


class TestGetMessages(unittest.IsolatedAsyncioTestCase):
    """get_messages — MOTD delivery, dedup, force bypass. 
    DB calls are mocked to ensure pure, isolated logic testing."""

    async def test_first_poll_delivers_one_message(self):
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=True), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A)
            self.assertEqual(len(result), 1)

    async def test_result_has_required_fields(self):
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=True), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A)
            msg = result[0]
            for field in ("id", "title", "body", "type", "context"):
                self.assertIn(field, msg)

    async def test_tags_field_stripped_from_output(self):
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=True), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A)
            self.assertNotIn("tags", result[0])

    async def test_second_poll_same_day_returns_empty(self):
        # Simulate DB reporting that MOTD is NOT due (already delivered)
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=False), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A)
            self.assertEqual(result, [])

    async def test_force_bypasses_dedup(self):
        # Simulate DB reporting that MOTD is NOT due, but we use force=True
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=False), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A, force=True) 
            self.assertEqual(len(result), 1)

    async def test_context_filter_motd(self):
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=True), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A, context="motd")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["context"], "motd")

    async def test_context_filter_no_match_returns_empty(self):
        with patch("core.messages.messages_service.get_or_create_session", new_callable=AsyncMock), \
             patch("core.messages.messages_service.motd_due", new_callable=AsyncMock, return_value=True), \
             patch("core.messages.messages_service.record_motd_delivered", new_callable=AsyncMock):
            
            result = await get_messages(DEVICE_A, context="alert")
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
