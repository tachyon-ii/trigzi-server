#!/usr/bin/env python3
from __future__ import annotations
#
#  tests/test_messages.py
#
#  Unit tests for core/messages/messages_service.py
#  No network calls, no DB, no server required.
#

import datetime
import hashlib
import unittest
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.messages import messages_service
from core.messages.messages_service import (
    get_messages,
    reset_device,
    reset_all,
    _pick_daily,
    _eligible,
    _date_ordinal,
)

DEVICE_A = "550e8400-e29b-41d4-a716-446655440000"
DEVICE_B = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def teardown_each(test):
    """Decorator: reset dedup store before each test method."""
    def wrapper(self):
        reset_all()
        test(self)
    return wrapper


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
        today_ordinal   = _date_ordinal()
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


class TestGetMessages(unittest.TestCase):
    """get_messages — MOTD delivery, dedup, force bypass."""

    def setUp(self):
        reset_all()

    def test_first_poll_delivers_one_message(self):
        result = get_messages(DEVICE_A)
        self.assertEqual(len(result), 1)

    def test_result_has_required_fields(self):
        result = get_messages(DEVICE_A)
        msg = result[0]
        for field in ("id", "title", "body", "type", "context"):
            self.assertIn(field, msg)

    def test_tags_field_stripped_from_output(self):
        result = get_messages(DEVICE_A)
        self.assertNotIn("tags", result[0])

    def test_second_poll_same_day_returns_empty(self):
        get_messages(DEVICE_A)
        result = get_messages(DEVICE_A)
        self.assertEqual(result, [])

    def test_force_bypasses_dedup(self):
        get_messages(DEVICE_A)                        # first — marks seen
        result = get_messages(DEVICE_A, force=True)   # force — should still deliver
        self.assertEqual(len(result), 1)

    def test_force_returns_same_message_as_first_poll(self):
        first  = get_messages(DEVICE_A)[0]["id"]
        forced = get_messages(DEVICE_A, force=True)[0]["id"]
        self.assertEqual(first, forced)

    def test_force_does_not_corrupt_dedup_state(self):
        """force=True must not add to _seen_store."""
        get_messages(DEVICE_A, force=True)
        # A normal poll after force should still deliver (not blocked by force call)
        result = get_messages(DEVICE_A)
        self.assertEqual(len(result), 1)

    def test_different_devices_independent_dedup(self):
        get_messages(DEVICE_A)
        result_b = get_messages(DEVICE_B)
        self.assertEqual(len(result_b), 1)

    def test_context_filter_motd(self):
        result = get_messages(DEVICE_A, context="motd")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["context"], "motd")

    def test_context_filter_no_match_returns_empty(self):
        result = get_messages(DEVICE_A, context="alert")
        self.assertEqual(result, [])

    def test_same_device_same_day_same_message_id(self):
        """Multiple force polls must return the same ID — not random."""
        ids = {get_messages(DEVICE_A, force=True)[0]["id"] for _ in range(5)}
        self.assertEqual(len(ids), 1)

    def test_missing_device_id_handled(self):
        """Empty string device_id should not crash — returns a result or empty."""
        try:
            result = get_messages("")
            self.assertIsInstance(result, list)
        except Exception as e:
            self.fail(f"get_messages raised unexpectedly: {e}")


class TestResetDevice(unittest.TestCase):
    """reset_device — clears one device without affecting others."""

    def setUp(self):
        reset_all()

    def test_reset_allows_redelivery(self):
        get_messages(DEVICE_A)
        reset_device(DEVICE_A)
        result = get_messages(DEVICE_A)
        self.assertEqual(len(result), 1)

    def test_reset_does_not_affect_other_devices(self):
        get_messages(DEVICE_A)
        get_messages(DEVICE_B)
        reset_device(DEVICE_A)
        # B is still marked seen
        result_b = get_messages(DEVICE_B)
        self.assertEqual(result_b, [])

    def test_reset_unknown_device_is_safe(self):
        try:
            reset_device("nonexistent-device-id")
        except Exception as e:
            self.fail(f"reset_device raised unexpectedly: {e}")


class TestResetAll(unittest.TestCase):
    """reset_all — clears all dedup state."""

    def test_reset_all_allows_redelivery_for_all_devices(self):
        get_messages(DEVICE_A)
        get_messages(DEVICE_B)
        reset_all()
        self.assertEqual(len(get_messages(DEVICE_A)), 1)
        self.assertEqual(len(get_messages(DEVICE_B)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
