#!/usr/bin/env python3
"""
=============================================================================
Script:        Image Miner
Location:      scripts/image_miner.py
Description:   Downloads product images from source URLs stored in the
               MySQL `product` table and saves them to the d4/d4 shard
               tree on disk.

Shard scheme (from trigzi-off/docs/GTIN_SHARD.md):
    images/{gtin[0:4]}/{gtin[4:8]}/{gtin}.jpg
    e.g. 9312345678901 → images/9312/3456/9312345678901.jpg

Rate limiting:
    --rate  requests per second (default: 1.0)
    Jitter of ±20% is applied to each interval to be polite.
    At 1 req/s with typical response latency the full AU dataset
    (~85k images) takes roughly 1–2 days.

Resume safety:
    img_downloaded column in MySQL tracks state:
        0 = pending
        1 = done
        2 = 404 / image unavailable
        3 = failed (network error, bad content-type, etc.)
    Re-running the script skips rows where img_downloaded != 0.
    Use --retry-failed to also retry state 3 rows.

Usage:
    # AU products first (default)
    python scripts/image_miner.py

    # Specific country
    python scripts/image_miner.py --country AU

    # All countries
    python scripts/image_miner.py --country ALL

    # Faster (less polite)
    python scripts/image_miner.py --rate 2

    # Retry previously failed downloads
    python scripts/image_miner.py --retry-failed

    # Dry run — print what would be downloaded
    python scripts/image_miner.py --dry-run --limit 20
=============================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp
import aiomysql

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("image_miner")

# ── Configuration ─────────────────────────────────────────────────────────────

IMAGE_ROOT  = Path(os.environ.get("IMAGE_ROOT",  "/var/www/trigzi/images"))
DB_HOST     = os.environ.get("DB_HOST",  "localhost")
DB_PORT     = int(os.environ.get("DB_PORT",  3306))
DB_USER     = os.environ.get("DB_USER",  "trigzi")
DB_PASS     = os.environ.get("DB_PASS",  "")
DB_NAME     = os.environ.get("DB_NAME",  "trigzi")

ACCEPTED_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
}

# ── GTIN shard path ───────────────────────────────────────────────────────────

def shard_path(gtin_int: int, root: Path = IMAGE_ROOT) -> Path:
    """
    Map an integer EAN-13 to its d4/d4 shard path.
    Always zero-pads to 13 digits.
    e.g. 9312345678901 → <root>/9312/3456/9312345678901.jpg
    """
    gtin_str = str(gtin_int).zfill(13)
    return root / gtin_str[0:4] / gtin_str[4:8] / f"{gtin_str}.jpg"


# ── Database helpers ──────────────────────────────────────────────────────────

async def make_pool() -> aiomysql.Pool:
    return await aiomysql.create_pool(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS, db=DB_NAME,
        autocommit=True,
        cursorclass=aiomysql.DictCursor,
        minsize=1, maxsize=4,
    )


async def fetch_pending(
    pool:          aiomysql.Pool,
    country_code:  Optional[str],
    retry_failed:  bool,
    limit:         Optional[int],
) -> list[dict]:
    """Return rows that still need an image, ordered AU-first then by GTIN."""
    states = [0]
    if retry_failed:
        states.append(3)

    placeholders = ",".join(["%s"] * len(states))

    country_clause = ""
    params: list = list(states)

    if country_code and country_code != "ALL":
        country_clause = "AND country_code = %s"
        params.append(country_code)

    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    sql = f"""
        SELECT gtin, img_url, country_code
        FROM   product
        WHERE  img_url IS NOT NULL
          AND  img_downloaded IN ({placeholders})
          {country_clause}
        ORDER BY
            CASE country_code WHEN 'AU' THEN 0 ELSE 1 END,
            gtin
        {limit_clause}
    """

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def mark_downloaded(pool: aiomysql.Pool, gtin_int: int, state: int) -> None:
    """Update img_downloaded state for a product row."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE products SET img_downloaded = %s WHERE gtin = %s",
                (state, gtin_int),
            )


# ── Downloader ────────────────────────────────────────────────────────────────

async def download_one(
    session:  aiohttp.ClientSession,
    pool:     aiomysql.Pool,
    row:      dict,
    dry_run:  bool,
) -> str:
    """
    Download one image and write it to the shard tree.

    Returns a short status string for logging:
        'ok' | 'exists' | '404' | 'bad_type' | 'error'
    """
    gtin_int = row["gtin"]
    url      = row["img_url"]
    dest     = shard_path(gtin_int)

    if dest.exists():
        await mark_downloaded(pool, gtin_int, 1)
        return "exists"

    if dry_run:
        log.info("DRY  %s → %s", gtin_int, url)
        return "dry"

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 404:
                await mark_downloaded(pool, gtin_int, 2)
                return "404"

            if resp.status != 200:
                log.warning("HTTP %d for gtin=%s", resp.status, gtin_int)
                await mark_downloaded(pool, gtin_int, 3)
                return "error"

            content_type = (resp.content_type or "").split(";")[0].strip().lower()
            if content_type not in ACCEPTED_CONTENT_TYPES:
                log.warning("bad content-type %r for gtin=%s", content_type, gtin_int)
                await mark_downloaded(pool, gtin_int, 3)
                return "bad_type"

            data = await resp.read()

        # Write atomically: temp file → rename
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(dest)

        await mark_downloaded(pool, gtin_int, 1)
        return "ok"

    except asyncio.TimeoutError:
        log.warning("timeout gtin=%s url=%s", gtin_int, url)
        await mark_downloaded(pool, gtin_int, 3)
        return "error"
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("error gtin=%s: %s", gtin_int, exc)
        await mark_downloaded(pool, gtin_int, 3)
        return "error"


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    pool = await make_pool()
    log.info("Connected to MySQL %s@%s/%s", DB_USER, DB_HOST, DB_NAME)

    rows = await fetch_pending(
        pool,
        country_code=args.country if args.country != "ALL" else None,
        retry_failed=args.retry_failed,
        limit=args.limit,
    )

    total = len(rows)
    if total == 0:
        log.info("Nothing to download.")
        pool.close()
        await pool.wait_closed()
        return

    log.info("Queued: %d images  rate: %.1f req/s  dry_run: %s",
             total, args.rate, args.dry_run)

    interval   = 1.0 / args.rate
    counters   = {"ok": 0, "exists": 0, "404": 0, "bad_type": 0, "error": 0, "dry": 0}

    headers = {
        "User-Agent": "Trigzi-ImageMiner/1.0 (+https://trigzi.com)",
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        for i, row in enumerate(rows, 1):
            t_start = time.monotonic()

            status = await download_one(session, pool, row, args.dry_run)
            counters[status] = counters.get(status, 0) + 1

            if i % 100 == 0 or i == total:
                log.info(
                    "Progress %d/%d  ok=%d exists=%d 404=%d err=%d",
                    i, total,
                    counters["ok"], counters["exists"],
                    counters["404"], counters["error"],
                )

            # Rate limiting with ±20% jitter
            elapsed  = time.monotonic() - t_start
            jitter   = interval * random.uniform(-0.2, 0.2)
            sleep_for = max(0.0, interval + jitter - elapsed)
            if sleep_for > 0 and not args.dry_run:
                await asyncio.sleep(sleep_for)

    pool.close()
    await pool.wait_closed()

    log.info(
        "Done. ok=%d  exists=%d  404=%d  bad_type=%d  error=%d",
        counters["ok"], counters["exists"],
        counters["404"], counters["bad_type"], counters["error"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigzi image miner")
    parser.add_argument("--country",      default="AU",
                        help="Country code to prioritise (default AU, or ALL)")
    parser.add_argument("--rate",         type=float, default=1.0,
                        help="Download rate in requests/second (default 1.0)")
    parser.add_argument("--limit",        type=int, default=None,
                        help="Stop after N downloads (useful for testing)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Also retry rows with img_downloaded=3 (previous failures)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Print what would be downloaded without fetching")
    args = parser.parse_args()

    if not DB_PASS:
        print("ERROR: DB_PASS not set. Source /etc/trigzi/env first.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
