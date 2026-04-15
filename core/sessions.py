#!/usr/bin/env python3
from __future__ import annotations
#
#  core/sessions.py
#
#  Session service for Trigzi.
#
#  Owns all reads and writes to the `sessions` table.
#  One row per device_id. Create-or-update on every request via
#  INSERT ... ON DUPLICATE KEY UPDATE so callers never need to
#  distinguish between new and returning devices.
#
#  Responsibilities:
#    - Upsert session on arrival (last_seen_at, ip, app_version)
#    - MOTD dedup: motd_last_date compared to CURRENT_DATE
#    - Token budget: daily counter with lazy reset on date rollover
#    - Tier enforcement: lazy lapse of paid → free on expiry
#
#  Usage:
#      from core.sessions import get_or_create_session, record_motd_delivered
#
#      session = await get_or_create_session(device_id, ip=ip, app_version=app_version)
#
#      if await motd_due(device_id):
#          # deliver message
#          await record_motd_delivered(device_id)
#

import logging
from typing import Optional

from core.db import get_pool

logger = logging.getLogger(__name__)

# Token budgets by tier
_BUDGET = {
    "free": 50_000,
    "paid": 500_000,
}


# ── Upsert ─────────────────────────────────────────────────────────────────────

async def get_or_create_session(
    device_id:   str,
    ip:          Optional[str] = None,
    app_version: Optional[str] = None,
) -> dict:
    """
    Upsert a session row for this device and return it.

    On first visit: inserts a new row with tier=free defaults.
    On return visit: updates last_seen_at, ip_last, app_version.
    Always returns the current row as a dict.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                INSERT INTO sessions (device_id, ip_last, app_version)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    last_seen_at = CURRENT_TIMESTAMP,
                    ip_last      = COALESCE(%s, ip_last),
                    app_version  = COALESCE(%s, app_version)
                """,
                (device_id, ip, app_version, ip, app_version),
            )

            await cur.execute(
                "SELECT * FROM sessions WHERE device_id = %s",
                (device_id,),
            )
            row = await cur.fetchone()

    # Lazy tier lapse: if paid has expired, treat as free for this request.
    # (Does not write back — a separate billing job can do the hard update.)
    if row["tier"] == "paid" and row["tier_expires_at"] is not None:
        from datetime import datetime
        if row["tier_expires_at"] < datetime.now():
            row = dict(row)
            row["tier"] = "free"

    logger.debug(f"Session: device={device_id[:8]}… tier={row['tier']}")
    return row


# ── MOTD dedup ─────────────────────────────────────────────────────────────────

async def motd_due(device_id: str) -> bool:
    """
    True if this device has not yet received today's MOTD.

    Condition: motd_last_date IS NULL OR motd_last_date < CURRENT_DATE
    The message ID itself is never stored — it is deterministic from
    (date, device_id) and always recoverable.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT motd_last_date < CURRENT_DATE OR motd_last_date IS NULL AS due
                FROM sessions
                WHERE device_id = %s
                """,
                (device_id,),
            )
            row = await cur.fetchone()

    if row is None:
        return True   # no session yet — will be created by get_or_create_session
    return bool(row["due"])


async def record_motd_delivered(device_id: str) -> None:
    """Stamp today's date as the last MOTD delivery for this device."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE sessions
                SET motd_last_date = CURRENT_DATE
                WHERE device_id = %s
                """,
                (device_id,),
            )
    logger.debug(f"Session: MOTD stamped for device={device_id[:8]}…")


# ── Token budget ───────────────────────────────────────────────────────────────

async def check_token_budget(device_id: str, tokens_requested: int) -> bool:
    """
    True if the device has enough remaining budget for this request.

    Lazily resets the daily counter if tokens_reset_date < TODAY.
    Does not consume tokens — call consume_tokens() after a successful LLM call.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:

            # Lazy daily reset
            await cur.execute(
                """
                UPDATE sessions
                SET tokens_used_today = 0,
                    tokens_reset_date = CURRENT_DATE
                WHERE device_id = %s
                  AND tokens_reset_date < CURRENT_DATE
                """,
                (device_id,),
            )

            await cur.execute(
                """
                SELECT tokens_used_today, tokens_budget_daily
                FROM sessions
                WHERE device_id = %s
                """,
                (device_id,),
            )
            row = await cur.fetchone()

    if row is None:
        return True   # no session yet, allow through
    remaining = row["tokens_budget_daily"] - row["tokens_used_today"]
    return remaining >= tokens_requested


async def consume_tokens(device_id: str, tokens_used: int) -> None:
    """Increment today's token counter by tokens_used."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE sessions
                SET tokens_used_today = tokens_used_today + %s
                WHERE device_id = %s
                """,
                (tokens_used, device_id),
            )
    logger.debug(f"Session: +{tokens_used} tokens for device={device_id[:8]}…")
