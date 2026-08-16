#!/usr/bin/env python3
"""
=============================================================================
Module:        Quart ASGI Application (Transport Layer)
Location:      app.py
Description:   The HTTP and Server-Sent Events (SSE) streaming API for Trigzi.

Architecture Note:
This file is strictly a TRANSPORT layer. It manages network
requests, payload extraction, and yields SSE event chunks.

It MUST NOT contain any database manipulation, file I/O, or
business logic.
- DB Reads: Delegated to `core/data_manager.py`
- DB Writes: Delegated to `core/enricher.py`
- LLM Orchestration: Delegated to `core/analyser.py`

Concurrency:
Runs on the Hypercorn ASGI server. Any blocking operations must
be explicitly offloaded to background threads to prevent starving
the async event loop and dropping client connections.
=============================================================================
"""

from __future__ import annotations

import os
import json
import logging
import asyncio

from quart import Quart, jsonify, request, Response

from core import data_manager, sqlite_store, swap as swap_engine
from core.db import init_pool, close_pool
from core.enricher import enrich, patch_nutrition
from core.analyser import (
    analyse_product,
    analyse_meal,
    analyse_menu,
    chat_assistant,
    chat_emoji,
    onboarding_assistant,
    sigmund_assistant,
    enrich_nutrition,
)
from core.telemetry import telemetry_bp, log_ocr_scan, log_menu_scan
from core.messages.messages_service import get_messages

# --- BOMB-PROOF FILE LOGGING ---
os.makedirs('/var/www/trigzi/logs', exist_ok=True)
logging.basicConfig(
    filename='/var/www/trigzi/logs/api.log',
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True # Override any Quart defaults
)
logger = logging.getLogger(__name__)

app = Quart(__name__)
app.register_blueprint(telemetry_bp)

MIN_GTIN_LEN = 8
MAX_GTIN_LEN = 14

@app.before_serving
async def startup():
    """Run once before the server starts accepting requests."""
    await init_pool()
    sqlite_store.open_all()   # gtin_cache.db, ingredient.db, content.db

    # Offload the heavy synchronous MinHash load to a background thread
    # so the ASGI loop can immediately bind to port 5000.
    asyncio.create_task(asyncio.to_thread(swap_engine.load))

@app.after_serving
async def shutdown():
    """Run once when the server is shutting down."""
    await close_pool()
    sqlite_store.close_all()

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

@app.route('/api/v1/product/<gtin>', methods=['GET'])
async def get_product(gtin):
    """GET a product by GTIN. Returns 404, complete JSON, or an SSE enrichment stream."""
    logger.info("Scan: %s", gtin)

    if not gtin.isdigit() or not MIN_GTIN_LEN <= len(gtin) <= MAX_GTIN_LEN:
        return jsonify({"error": "Invalid barcode."}), 400

    record = await data_manager.get_product(gtin)

    if not record:
        return jsonify({"status": "not_found", "gtin": gtin}), 404

    if data_manager.is_enriched(record):
        return jsonify({"status": "complete", "product": record}), 200

    async def generate():
        try:
            name  = record.get("name",  "this product")
            brand = record.get("brand", "")
            label = f"{name} by {brand}" if brand else name
            yield _sse("progress", {"message": f"Found {label}"})

            yield _sse("progress", {"message": "Running latest analytics…"})

            enriched = await enrich(record)
            yield _sse("enriched", {"status": "complete", "product": enriched})

        except Exception as e:
            logger.error("Product enrichment stream crashed: %s", str(e), exc_info=True)
            yield _sse("error", {"message": "Analytics failed. Please try scanning again."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.route('/api/v1/analyse/product', methods=['POST'])
async def analyse_product_route():
    """POST OCR-extracted label/nutrition text for an analysed dietary verdict."""
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    gtin           = data.get('gtin', '').strip()
    text_front     = data.get('text_front', '').strip()
    text_nutrition = data.get('text_nutrition', '').strip()

    if not gtin:
        return jsonify({"error": "Missing gtin."}), 400

    logger.info("Analyse product: %s", gtin)
    log_ocr_scan(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)

    result = await analyse_product(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)
    if not result:
        logger.warning("Analysis failed for product %s - Unprocessable OCR.", gtin)
        return jsonify({"error": "Could not extract product data. Please check scan quality."}), 422

    return jsonify({"status": "ok", "result": result}), 200

@app.route('/api/v1/analyse/meal', methods=['POST'])
async def analyse_meal_route():
    """POST a base64-encoded meal photo with optional dietary profile for analysis."""
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    image = data.get('image', '')
    profile = data.get('profile', {})

    if isinstance(image, str):
        image = image.strip()
        # Defensively strip Data URI prefix if the client includes it
        if "," in image and image.startswith("data:image"):
            image = image.split(",", 1)[1]

    if not image:
        return jsonify({"error": "Missing image."}), 400

    logger.info("Analyse meal (%d chars base64)", len(image))

    profile_str = json.dumps(profile) if isinstance(profile, dict) else str(profile)

    result = await analyse_meal(image=image, profile=profile_str)
    if not result:
        logger.warning("Analysis failed for meal photo - Unprocessable Image.")
        return jsonify({"error": "Could not analyze meal. Please ensure the image is clear."}), 422

    return jsonify({"status": "ok", "result": result}), 200

@app.route('/api/v1/analyse/menu', methods=['POST'])
async def analyse_menu_route():
    """POST OCR-extracted menu text for dish/ingredient extraction."""
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    text = data.get('text', '')

    if isinstance(text, str):
        text = text.strip()
    if not text:
        logger.error("Missing text in menu request")
        return jsonify({"error": "Missing text."}), 400

    saved_file = log_menu_scan(text)
    logger.info("Analyse menu (%d chars) -> Saved to %s", len(text), saved_file)

    result = await analyse_menu(text=text)
    if not result:
        logger.warning("Analysis failed for menu - Unprocessable OCR.")
        return jsonify({"error": "Could not extract menu items. Please ensure the text is clear."}), 422

    return jsonify({"status": "ok", "result": result}), 200

@app.route('/api/v1/chat/stream', methods=['POST'])
async def chat_stream_endpoint():
    """POST a chat message and stream the clinical-assistant response as SSE events."""
    try:
        data = await request.get_json()
    except Exception as e:
        print("❌ Failed to parse incoming JSON: %s", e)
        return jsonify({"error": "Invalid payload."}), 400

    system_context  = data.get('system_context', {})
    history         = data.get('history', [])
    message         = data.get('message', '')
    trigzi_nickname = data.get('trigzi_nickname', 'Trigzi')

    if isinstance(message, str):
        message = message.strip()

    if not message:
        logger.error("Missing message in chat stream request")
        return jsonify({"error": "Missing message."}), 400

    logger.info("Chat stream request received: %s...", message[:30])

    async def generate():
        try:
            text, action_cmd = await chat_assistant(system_context, history, message, trigzi_nickname)

            if not text:
                yield _sse("error", {"message": "Analysis failed."})
                return

            clean_text = text.replace("Message: ", "").replace("Message:", "").replace("\nAction:", "").replace("Action:", "").strip()

            if clean_text:
                yield _sse("text", {"content": clean_text})

            if action_cmd:
                tool_parts = action_cmd.split("|", 1)
                tool = tool_parts[0].strip()
                param = tool_parts[1].strip() if len(tool_parts) > 1 else ""
                yield _sse("action", {"tool": tool, "param": param})

            if clean_text:
                emoji = await chat_emoji(clean_text)
                if emoji:
                    yield _sse("emoji", {"content": f" {emoji}"})

            yield _sse("done", {})

        except Exception as e:
            logger.error("Stream crashed: %s", str(e), exc_info=True)
            yield _sse("error", {"message": "An unexpected server error occurred."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.route('/api/v1/chat/emoji', methods=['POST'])
async def chat_emoji_route():
    """POST text content; return a single contextually-appropriate emoji."""
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    text = data.get('text', '')
    if isinstance(text, str):
        text = text.strip()

    if not text:
        return jsonify({"error": "Missing 'text' field to analyse."}), 400

    emoji = await chat_emoji(text)

    return jsonify({
        "status": "ok",
        "emoji": emoji
    }), 200

@app.route('/api/v1/chat/onboarding', methods=['POST'])
async def chat_onboarding_route():
    """POST an onboarding message; stream scripted-onboarding events as SSE."""
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    message = data.get('message', '')
    fallback_name = data.get('fallback_name', 'Zesty Koala')
    trigzi_nickname = data.get('trigzi_nickname', 'Trigzi')

    if isinstance(message, str):
        message = message.strip()

    if not message:
        logger.error("Missing message in onboarding stream request")
        return jsonify({"error": "Missing message."}), 400

    logger.info("Onboarding stream request received: %s...", message[:30])

    async def generate():
        try:
            events, text_content = await onboarding_assistant(message, fallback_name, trigzi_nickname)

            for evt in events:
                yield _sse(evt["event"], evt["data"])

            if text_content:
                emoji = await chat_emoji(text_content)
                if emoji:
                    yield _sse("emoji", {"content": f" {emoji}"})

            yield _sse("done", {})

        except Exception as e:
            logger.error("Stream crashed: %s", str(e), exc_info=True)
            yield _sse("error", {"message": "Analysis failed."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.route('/api/v1/chat/sigmund', methods=['POST'])
async def chat_sigmund_endpoint():
    """POST a chat message; stream the high-EQ Sigmund-intercept response as SSE."""
    try:
        data = await request.get_json()
    except Exception as e:
        print("❌ Failed to parse incoming JSON: %s", e)
        return jsonify({"error": "Invalid payload."}), 400

    system_context  = data.get('system_context', {})
    history         = data.get('history', [])
    message         = data.get('message', '')

    if isinstance(message, str):
        message = message.strip()

    if not message:
        logger.error("Missing message in Sigmund request")
        return jsonify({"error": "Missing message."}), 400

    async def generate():
        try:
            text, action_cmd = await sigmund_assistant(system_context, history, message)

            if not text:
                yield _sse("error", {"message": "Analysis failed."})
                return

            yield _sse("text", {"content": text})

            if action_cmd:
                tool_parts = action_cmd.split("|", 1)
                tool = tool_parts[0].strip()
                param = tool_parts[1].strip() if len(tool_parts) > 1 else ""
                yield _sse("action", {"tool": tool, "param": param})

            yield _sse("done", {})

        except Exception as e:
            logger.error("Sigmund stream crashed: %s", str(e), exc_info=True)
            yield _sse("error", {"message": "An unexpected server error occurred."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.route('/api/v1/enrich/nutrition', methods=['POST'])
async def enrich_nutrition_route():
    """POST OCR-extracted nutrition panel; extract structured nutrition and patch DB."""
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    gtin = data.get('gtin', '').strip()
    ocr_text = data.get('ocr_text', '').strip()

    if not gtin or not ocr_text:
        return jsonify({"error": "Missing gtin or ocr_text."}), 400

    logger.info("Enrich nutrition requested for GTIN: %s", gtin)

    nutrition_data = await enrich_nutrition(gtin, ocr_text)

    if not nutrition_data:
        logger.warning("Nutrition extraction failed for GTIN: %s - Unprocessable OCR.", gtin)
        return jsonify({"error": "Failed to parse nutrition data. Please ensure the scan is clear."}), 422

    patched = await patch_nutrition(gtin, nutrition_data)
    if patched:
        logger.info("Successfully patched nutrition data for GTIN: %s in global database.", gtin)
    else:
        logger.warning("Could not find GTIN %s in database to patch, returning payload to client anyway.", gtin)

    return jsonify(nutrition_data), 200

@app.route('/api/v1/messages', methods=['GET'])
async def messages_route():
    """GET today's MOTD for an authenticated device. Empty list if already delivered today."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Unauthorized."}), 401

    device_id = request.headers.get("X-Device-ID", "").strip()
    if not device_id:
        return jsonify({"error": "Missing X-Device-ID."}), 400

    since_raw   = request.args.get("since")
    since       = int(since_raw) if since_raw and since_raw.isdigit() else None
    context     = request.args.get("context")
    force       = request.args.get("force", "0") == "1"
    ip          = request.remote_addr
    app_version = request.headers.get("X-App-Version")

    logger.info("Messages: device=%s… context=%s force=%s", device_id[:8], context, force)

    messages = await get_messages(
        device_id   = device_id,
        since       = since,
        context     = context,
        force       = force,
        ip          = ip,
        app_version = app_version,
    )
    return jsonify(messages), 200

@app.route('/api/v1/swap/<gtin>', methods=['GET'])
async def swap_route(gtin):
    """
    GET similar-but-safe product alternatives for a scanned GTIN.

    Query params:
        avoid  — comma-separated canonical IDs the user is sensitive to
        n      — max results (default 10, max 20)

    Returns:
        {"status": "ok", "alternatives": [...]}
        {"status": "unavailable"} if the swap index is not loaded
        {"status": "not_found"}   if the GTIN is unknown
    """
    if not swap_engine.is_available():
        return jsonify({"status": "unavailable",
                        "message": "Swap index not loaded."}), 503

    gtin_str = gtin.strip()
    if not gtin_str.isdigit() or not MIN_GTIN_LEN <= len(gtin_str) <= MAX_GTIN_LEN:
        return jsonify({"error": "Invalid barcode."}), 400

    from utils.gtin import normalise  # pylint: disable=import-outside-toplevel
    canonical_str = normalise(gtin_str)
    if not canonical_str:
        return jsonify({"error": "Invalid barcode."}), 400
    gtin_int = int(canonical_str)

    # Parse avoid set
    avoid_raw = request.args.get("avoid", "")
    avoid_set: set[int] = set()
    for token in avoid_raw.split(","):
        token = token.strip()
        if token.isdigit():
            avoid_set.add(int(token))

    max_n = min(int(request.args.get("n", 10)), 20)

    logger.info("Swap: gtin=%s avoid=%d canonicals n=%d", gtin_int, len(avoid_set), max_n)

    alternatives = await swap_engine.query(gtin_int, avoid_set, max_results=max_n)

    if alternatives is None:
        return jsonify({"status": "not_found"}), 404

    return jsonify({"status": "ok", "alternatives": alternatives}), 200


@app.route('/api/v1/content/<canonical>', methods=['GET'])
async def content_route(canonical):
    """
    GET rich description for a canonical ingredient by name or numeric ID.

    Path param:
        canonical — canonical name (e.g. "soy_lecithin") or integer ID

    Query params:
        lang — BCP-47 language tag (default: en)

    Returns:
        {"status": "ok", "canonical": "...", "content": {summary, what, origin, why, avoid_if}}
        {"status": "not_found"} if the canonical is not in content.db
    """
    lang = request.args.get("lang", "en")

    canonical = canonical.strip()
    if canonical.isdigit():
        content = sqlite_store.get_content(int(canonical), lang)
    else:
        content = sqlite_store.get_content_by_name(canonical, lang)

    if not content:
        return jsonify({"status": "not_found"}), 404

    primary_name = content.pop("primary_name", canonical)
    return jsonify({
        "status":    "ok",
        "canonical": primary_name,
        "content":   content,
    }), 200


@app.route('/api/v1/product/image', methods=['POST'])
async def upload_product_image_route():
    """
    POST a user-captured product image for moderation review.

    Multipart form fields:
        gtin       — product barcode (required)
        image      — image file (required, JPEG or PNG, max 8 MB)
        image_type — front | ingredients | nutrition | back | other (default: front)

    The image is stored in a pending/ staging area and a user_image row is
    created with mod_state='pending'. It does NOT enter the product shard
    until a moderator approves it. This gate exists because user-submitted
    images have a 100% probability of abuse without moderation.

    Returns:
        {"status": "queued", "id": <user_image.id>}
    """
    import asyncio  # pylint: disable=import-outside-toplevel
    import hashlib  # pylint: disable=import-outside-toplevel
    import uuid     # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel

    MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB

    files = await request.files
    form  = await request.form

    gtin_raw = form.get("gtin", "").strip()
    if not gtin_raw:
        return jsonify({"error": "Missing gtin."}), 400

    from utils.gtin import normalise  # pylint: disable=import-outside-toplevel
    gtin_str = normalise(gtin_raw)
    if not gtin_str:
        return jsonify({"error": "Invalid gtin."}), 400
    gtin_int = int(gtin_str)

    img_file = files.get("image")
    if img_file is None:
        return jsonify({"error": "Missing image file."}), 400

    img_bytes = img_file.read()
    if len(img_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Image exceeds 8 MB limit."}), 413

    mime = img_file.mimetype or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        return jsonify({"error": "Unsupported image type. Use JPEG or PNG."}), 415

    image_type = form.get("image_type", "front")
    if image_type not in ("front", "ingredients", "nutrition", "back", "other"):
        image_type = "front"

    # Device identity — anonymised to first 16 hex of SHA256(device_id)
    device_id   = request.headers.get("X-Device-ID", "")
    device_hash = hashlib.sha256(device_id.encode()).hexdigest()[:16] if device_id else None

    # Save to pending staging area
    pending_root = Path(os.environ.get("IMAGE_ROOT", "/var/www/trigzi/images")) / "pending"
    pending_root.mkdir(parents=True, exist_ok=True)

    ext         = "jpg" if "jpeg" in mime else mime.split("/")[-1]
    filename    = f"{uuid.uuid4().hex}.{ext}"
    storage_key = f"pending/{filename}"
    dest        = pending_root / filename

    await asyncio.to_thread(dest.write_bytes, img_bytes)

    sha256 = hashlib.sha256(img_bytes).hexdigest()

    # Insert moderation row
    try:
        from core.db import get_pool  # pylint: disable=import-outside-toplevel
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO user_image
                        (gtin, storage_key, image_type, mime_type,
                         size_bytes, sha256, device_hash, country_code)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (gtin_int, storage_key, image_type, mime,
                     len(img_bytes), sha256, device_hash, "AU"),
                )
                row_id = cur.lastrowid
        logger.info("user_image queued: id=%s gtin=%s type=%s", row_id, gtin_int, image_type)
        return jsonify({"status": "queued", "id": row_id}), 202
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("user_image insert failed: %s", exc)
        return jsonify({"error": "Failed to queue image."}), 500


@app.route('/api/trigger-500', methods=['GET'])
async def trigger_500_route():
    """Deliberate failure endpoint to test Nginx 500 JSON error handling."""
    logger.warning("Triggering intentional divide-by-zero error for testing.")
    fail = 1 / 0
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    from hypercorn.config import Config
    from hypercorn.asyncio import serve
    import asyncio
    config = Config()
    config.bind = ["127.0.0.1:5000"]
    asyncio.run(serve(app, config))
