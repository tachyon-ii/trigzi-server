#!/usr/bin/env python3
from __future__ import annotations
#
#  app.py
#  trigzi
#
#  Quart ASGI application — async end-to-end.
#  Served by Hypercorn (replaces Gunicorn).
#
#  Routes:
#    GET  /api/v1/product/<gtin>      — product lookup (JSON or SSE)
#    POST /api/v1/analyse/product     — unknown product OCR analysis
#    POST /api/v1/analyse/meal        — meal photo analysis
#    POST /api/v1/analyse/menu        — menu OCR text analysis
#

from quart import Quart, jsonify, request, Response
from core import data_manager
from core.enricher import enrich
from core.analyser import analyse_product, analyse_meal, analyse_menu
from core.telemetry import telemetry_bp, log_ocr_scan
import json
import time

app = Quart(__name__)
app.register_blueprint(telemetry_bp)

MIN_GTIN_LEN = 8
MAX_GTIN_LEN = 14


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route('/api/v1/product/<gtin>', methods=['GET'])
async def get_product(gtin):
    print(f"[{time.strftime('%H:%M:%S')}] Scan: {gtin}")

    if not gtin.isdigit() or not (MIN_GTIN_LEN <= len(gtin) <= MAX_GTIN_LEN):
        return jsonify({"error": "Invalid barcode."}), 400

    record = data_manager.get_product(gtin)

    if not record:
        return jsonify({"status": "not_found", "gtin": gtin}), 404

    if data_manager.is_enriched(record):
        return jsonify({"status": "complete", "product": record}), 200

    async def generate():
        yield _sse("partial",  {"status": "partial",  "product": record})
        enriched = await enrich(record)
        yield _sse("enriched", {"status": "complete", "product": enriched})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })


@app.route('/api/v1/analyse/product', methods=['POST'])
async def analyse_product_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    gtin           = data.get('gtin', '').strip()
    text_front     = data.get('text_front', '').strip()
    text_nutrition = data.get('text_nutrition', '').strip()

    if not gtin:
        return jsonify({"error": "Missing gtin."}), 400

    print(f"[{time.strftime('%H:%M:%S')}] Analyse product: {gtin}")
    log_ocr_scan(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)

    result = await analyse_product(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)
    if not result:
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200


@app.route('/api/v1/analyse/meal', methods=['POST'])
async def analyse_meal_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    image   = data.get('image', '').strip()
    profile = data.get('profile', '').strip()

    if not image:
        return jsonify({"error": "Missing image."}), 400

    print(f"[{time.strftime('%H:%M:%S')}] Analyse meal ({len(image)} chars base64)")

    result = await analyse_meal(image=image, profile=profile)
    if not result:
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200


@app.route('/api/v1/analyse/menu', methods=['POST'])
async def analyse_menu_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    text    = data.get('text', '').strip()
    profile = data.get('profile', '').strip()

    if not text:
        return jsonify({"error": "Missing text."}), 400

    print(f"[{time.strftime('%H:%M:%S')}] Analyse menu ({len(text)} chars)")

    result = await analyse_menu(text=text, profile=profile)
    if not result:
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200


if __name__ == '__main__':
    import asyncio
    from hypercorn.config import Config
    from hypercorn.asyncio import serve
    config = Config()
    config.bind = ["127.0.0.1:5000"]
    asyncio.run(serve(app, config))
