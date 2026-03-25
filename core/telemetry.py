from __future__ import annotations
"""
core/telemetry.py

Writes raw scan inputs to logs/scans/<timestamp>_<gtin>.txt
These files form the test corpus for prompt development.

Usage:
    from core.telemetry import log_scan, log_ocr_scan

    # OFF enrichment path
    log_scan(gtin="9300631751533", source="off", text=raw_ingredients)

    # OCR path
    log_ocr_scan(gtin="9300631751533", text_front=..., text_nutrition=...)
"""

import os
import time
from typing import Optional

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs', 'scans')


def _ensure_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def _ts() -> str:
    return str(int(time.time()))


def _write(filename: str, content: str) -> None:
    _ensure_dir()
    path = os.path.join(LOG_DIR, filename)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        print(f"  [!] telemetry write failed: {e}")


def log_scan(gtin: str, source: str, text: str) -> None:
    """Log an OFF/Woolworths/Coles enrichment input."""
    filename = f"{_ts()}_{gtin}_enrich.txt"
    content  = (
        f"GTIN: {gtin}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"SOURCE: {source}\n"
        f"\n"
        f"=== INGREDIENTS ===\n"
        f"{text}\n"
    )
    _write(filename, content)


def log_ocr_scan(gtin: str, text_front: str, text_nutrition: str) -> None:
    """Log a dual-capture OCR scan input."""
    filename = f"{_ts()}_{gtin}_ocr.txt"
    content  = (
        f"GTIN: {gtin}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"SOURCE: ocr\n"
        f"\n"
        f"=== FRONT OF PACKAGE ===\n"
        f"{text_front}\n"
        f"\n"
        f"=== NUTRITION & INGREDIENTS ===\n"
        f"{text_nutrition}\n"
    )
    _write(filename, content)
