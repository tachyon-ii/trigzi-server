#!/usr/bin/env python3
"""
core/telemetry.py
All telemetry logging and routes for Trigzi.
Register in app.py:
    from core.telemetry import telemetry_bp
    app.register_blueprint(telemetry_bp)
Logging helpers:
    log_scan()        — OFF/Woolworths/Coles enrichment input
    log_ocr_scan()    — dual-capture OCR scan input
    log_menu_scan()   — menu OCR scan input
    log_unmatched()   — unmatched GTIN (product acquisition queue)
    log_journey()     — cognitive timeline dump from developer sandbox
"""
from __future__ import annotations
import os
import time
import asyncio
from quart import Blueprint, request, jsonify

BASE_DIR      = os.path.join(os.path.dirname(__file__), '..')
SCANS_DIR     = os.path.join(BASE_DIR, 'logs', 'scans')
JOURNEYS_DIR  = os.path.join(BASE_DIR, 'logs', 'journeys')
UNMATCHED_LOG = os.path.join(BASE_DIR, 'logs', 'unmatched.log')

telemetry_bp = Blueprint('telemetry', __name__)

# --- Internal helpers ---
def _ts() -> str:
    return str(int(time.time()))

def _write_file_sync(directory: str, filename: str, content: str) -> None:
    os.makedirs(directory, exist_ok=True)
    try:
        with open(os.path.join(directory, filename), 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        print(f"  [!] telemetry write failed to {directory}: {e}")

def _write_scan(filename: str, content: str) -> None:
    asyncio.create_task(asyncio.to_thread(_write_file_sync, SCANS_DIR, filename, content))

def _write_journey(filename: str, content: str) -> None:
    asyncio.create_task(asyncio.to_thread(_write_file_sync, JOURNEYS_DIR, filename, content))

def _log_unmatched_sync(gtin: str) -> None:
    try:
        os.makedirs(os.path.dirname(UNMATCHED_LOG), exist_ok=True)
        with open(UNMATCHED_LOG, 'a', encoding='utf-8') as f:
            f.write(f"{gtin}\n")
    except OSError as e:
        print(f"  [!] unmatched log write failed: {e}")

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
    asyncio.create_task(asyncio.to_thread(_log_unmatched_sync, gtin))

def log_journey(idfv: str, dump: str) -> None:
    """Log a cognitive timeline dump from the developer sandbox."""
    filename = f"{_ts()}_{idfv}_journey.txt"
    _write_journey(filename, (
        f"IDFV: {idfv}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"\n"
        f"{dump}\n"
    ))

# --- Routes ---

@telemetry_bp.route('/api/v1/telemetry/unmatched', methods=['POST'])
@telemetry_bp.route('/api/v1/telemetry/unmatched/gtin', methods=['POST'])
@telemetry_bp.route('/api/v1/telemetry/unmatched/<gtin>', methods=['GET'])
async def telemetry_unmatched(gtin=None):
    """Log an unmatched GTIN so we can add it if common."""
    if gtin is None:
        data = await request.get_json(silent=True) or {}
        gtin = data.get('term', '').strip()
    if gtin:
        log_unmatched(gtin)
        print(f"[{time.strftime('%H:%M:%S')}] Unmatched: {gtin}")
    return jsonify({"status": "logged"}), 200

@telemetry_bp.route('/api/v1/telemetry/journey/<idfv>', methods=['POST'])
async def telemetry_journey(idfv):
    """Log a cognitive timeline dump from the iOS developer sandbox."""
    data = await request.get_json(silent=True) or {}
    dump = data.get('dump', '').strip()

    if idfv and dump:
        log_journey(idfv, dump)
        print(f"[{time.strftime('%H:%M:%S')}] Journey logged for device: {idfv[:8]}")

    return jsonify({"status": "logged"}), 200
