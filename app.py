from flask import Flask, jsonify, request, Response, stream_with_context
from core import data_manager
from core.telemetry import log_ocr_scan
import json
import time
import os

app = Flask(__name__)

MIN_GTIN_LEN = 8
MAX_GTIN_LEN = 14

def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route('/api/v1/product/<gtin>', methods=['GET'])
def get_product(gtin):
    print(f"[{time.strftime('%H:%M:%S')}] Scan: {gtin}")

    if not gtin.isdigit() or not (MIN_GTIN_LEN <= len(gtin) <= MAX_GTIN_LEN):
        return jsonify({"error": "Invalid barcode."}), 400

    record = data_manager.get_product(gtin)

    # Case 3: not found — plain JSON, no SSE needed
    if not record:
        return jsonify({"status": "not_found", "gtin": gtin}), 404

    # Case 1: already enriched — plain JSON, instant return
    if data_manager.is_enriched(record):
        return jsonify({"status": "complete", "product": record}), 200

    # Case 2: found but unenriched — SSE stream
    def generate():
        # Phase 1: immediate partial record
        yield _sse("partial", {"status": "partial", "product": record})

        # Phase 2: enrich (blocks this generator, ~2-5s)
        enriched = data_manager.enrich(record)
        yield _sse("enriched", {"status": "complete", "product": enriched})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':    'no-cache',
            'X-Accel-Buffering': 'no',      # critical: disable nginx buffering
            'Connection':        'keep-alive',
        }
    )


@app.route('/api/v1/analyse/product', methods=['POST'])
def analyse_product():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    gtin           = data.get('gtin', '').strip()
    text_front     = data.get('text_front', '').strip()
    text_nutrition = data.get('text_nutrition', '').strip()

    if not gtin:
        return jsonify({"error": "Missing gtin."}), 400

    print(f"[{time.strftime('%H:%M:%S')}] Analyse product: {gtin}")
    log_ocr_scan(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)

    result = data_manager.analyse_product(
        gtin           = gtin,
        text_front     = text_front,
        text_nutrition = text_nutrition,
    )

    if not result:
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
