#!/usr/bin/env python3
from __future__ import annotations
"""
core/telemetry.py

All telemetry logging and routes for Trigzi.

Register in app.py:
    from core.telemetry import telemetry_bp
    app.register_blueprint(telemetry_bp)

Logging helpers:
    log_scan()        — OFF/Woolworths/Coles enrichment input
    log_ocr_scan()    — dual-capture OCR scan input
    log_unmatched()   — unmatched GTIN (product acquisition queue)

Sort unmatched by frequency:
    sort logs/unmatched.log | uniq -c | sort -rn | head -50
"""

import os
import time
from quart import Blueprint, request, jsonify

BASE_DIR      = os.path.join(os.path.dirname(__file__), '..')
SCANS_DIR     = os.path.join(BASE_DIR, 'logs', 'scans')
UNMATCHED_LOG = os.path.join(BASE_DIR, 'logs', 'unmatched.log')

telemetry_bp = Blueprint('telemetry', __name__)


# --- Internal helpers ---

def _ts() -> str:
    return str(int(time.time()))


def _write_scan(filename: str, content: str) -> None:
    os.makedirs(SCANS_DIR, exist_ok=True)
    try:
        with open(os.path.join(SCANS_DIR, filename), 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        print(f"  [!] telemetry write failed: {e}")


# --- Public logging API ---

def log_scan(gtin: str, source: str, text: str) -> None:
    """Log an OFF/Woolworths/Coles enrichment input."""
    _write_scan(f"{_ts()}_{gtin}_enrich.txt", (
        f"GTIN: {gtin}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"SOURCE: {source}\n"
        f"\n"
        f"=== INGREDIENTS ===\n"
        f"{text}\n"
    ))


def log_ocr_scan(gtin: str, text_front: str, text_nutrition: str) -> None:
    """Log a dual-capture OCR scan input."""
    _write_scan(f"{_ts()}_{gtin}_ocr.txt", (
        f"GTIN: {gtin}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"SOURCE: ocr\n"
        f"\n"
        f"=== FRONT OF PACKAGE ===\n"
        f"{text_front}\n"
        f"\n"
        f"=== NUTRITION & INGREDIENTS ===\n"
        f"{text_nutrition}\n"
    ))

def log_menu_scan(text: str) -> str:
    """Log a raw OCR menu scan and return the filename for reference."""
    filename = f"{_ts()}_menu_scan.txt"
    _write_scan(filename, (
        f"TIMESTAMP: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"SOURCE: menu_ocr\n"
        f"\n"
        f"=== MENU TEXT ===\n"
        f"{text}\n"
    ))
    return filename

def log_unmatched(gtin: str) -> None:
    """Log an unmatched GTIN to the product acquisition queue."""
    try:
        os.makedirs(os.path.dirname(UNMATCHED_LOG), exist_ok=True)
        with open(UNMATCHED_LOG, 'a', encoding='utf-8') as f:
            f.write(f"{gtin}\n")
    except OSError as e:
        print(f"  [!] unmatched log write failed: {e}")


# --- Routes ---

@telemetry_bp.route('/api/v1/telemetry/unmatched', methods=['POST'])
@telemetry_bp.route('/api/v1/telemetry/unmatched/gtin', methods=['POST'])
@telemetry_bp.route('/api/v1/telemetry/unmatched/<gtin>', methods=['GET'])
async def telemetry_unmatched(gtin=None):
    if gtin is None:
        data = await request.get_json(silent=True) or {}
        gtin = data.get('term', '').strip()
    if gtin:
        log_unmatched(gtin)
        print(f"[{time.strftime('%H:%M:%S')}] Unmatched: {gtin}")
    return jsonify({"status": "logged"}), 200
