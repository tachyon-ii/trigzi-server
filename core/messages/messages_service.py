#!/usr/bin/env python3
"""
=============================================================================
Module:        Messages Service
Location:      core/messages/messages_service.py
Description:   Central delivery service for all server-side messages.
               Serves one message per day per device (MOTD semantics).
               Selection is deterministic on (date, device_id) so repeated
               polls within the same day always return the same message —
               no flicker, no surprises.

Architecture Note:
Dedup is owned by core/sessions.py via motd_due() and
record_motd_delivered() — a single DATE column in MariaDB, shared
across all Hypercorn workers, so two workers can't deliver the same
device's MOTD twice.

Adding a future source:
    1. Create core/messages/<source>.py with a QUOTES list.
    2. Import it here and append to _ALL_SOURCES.
=============================================================================
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Optional

from core.sessions import get_or_create_session, motd_due, record_motd_delivered
from .motd import QUOTES as _MOTD_QUOTES

logger = logging.getLogger(__name__)

# ── Source registry ────────────────────────────────────────────────────────────
_ALL_SOURCES: list[list[dict]] = [
    _MOTD_QUOTES,
    # _ALERT_MESSAGES,   # future
    # _PROMO_MESSAGES,   # future
]

_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _today() -> str:
    return _DAY_NAMES[datetime.datetime.now().weekday()]


def _date_ordinal() -> int:
    """Integer that increments once per calendar day. Used as selection seed."""
    return datetime.date.today().toordinal()


def _pick_daily(eligible: list[dict], device_id: str) -> Optional[dict]:
    """
    Select one message deterministically for today.

    Seed is date.toordinal() + md5(device_id), which means:
      - Same device, same day       → identical message no matter how many times iOS polls
      - Same day, different devices → different messages (avoids everyone getting motd-001 on launch day)
      - Next day                    → rotates automatically, no cron needed
    """
    if not eligible:
        return None
    seed = _date_ordinal() + int(hashlib.md5(device_id.encode()).hexdigest(), 16)
    return eligible[seed % len(eligible)]


def _eligible(msg: dict, today: str, context: Optional[str]) -> bool:
    """True if this message passes day-tag and context filters."""
    tags = msg.get("tags", [])
    if tags and today not in tags:
        return False
    if context and msg.get("context") != context:
        return False
    return True


def _serialise(msg: dict) -> dict:
    """Strip internal-only fields before handing to the route layer."""
    return {
        "id":      msg["id"],
        "title":   msg["title"],
        "body":    msg["body"],
        "type":    msg["type"],
        "context": msg.get("context"),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

async def get_messages(  # pylint: disable=too-many-arguments,too-many-positional-arguments,unused-argument
    device_id:   str,
    since:       Optional[int] = None,
    context:     Optional[str] = None,
    force:       bool = False,
    ip:          Optional[str] = None,
    app_version: Optional[str] = None,
) -> list[dict]:
    """
    Return today's single message for this device, or [] if already seen.

    Upserts the session row on every call (last_seen_at, ip, app_version).
    Dedup is delegated to core/sessions — motd_last_date compared to
    CURRENT_DATE, consistent across all Hypercorn workers.

    Args:
        device_id:   X-Device-ID header value. Primary key in sessions.
        since:       Unix timestamp. Reserved for future server-push alerts.
                     Currently unused (the server returns today's MOTD
                     unconditionally) — kept in the signature for forward
                     API stability so iOS clients that already send it
                     don't break when server-push lands.
        context:     Optional filter, e.g. "motd".
        force:       Skip deduplication. For dev/testing only.
        ip:          Request remote addr, stored on session for abuse detection.
        app_version: iOS build version from User-Agent or header.
    """
    # Always upsert session — keeps last_seen_at and ip current
    await get_or_create_session(device_id, ip=ip, app_version=app_version)

    today = _today()

    eligible = [
        msg for source in _ALL_SOURCES
        for msg in source
        if _eligible(msg, today, context)
    ]

    chosen = _pick_daily(eligible, device_id)
    if not chosen:
        return []

    if not force:
        due = await motd_due(device_id)
        if not due:
            logger.info("Messages: device=%s… already seen today", device_id[:8])
            return []

    if not force:
        await record_motd_delivered(device_id)

    logger.info("Messages: device=%s… delivering %s", device_id[:8], chosen['id'])
    return [_serialise(chosen)]
