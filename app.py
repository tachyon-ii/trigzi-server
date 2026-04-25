#!/usr/bin/env python3
from __future__ import annotations
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

import os
import json
import logging
from quart import Quart, jsonify, request, Response

from core import data_manager
from core.db import init_pool, close_pool
from core.enricher import enrich, patch_nutrition
from core.analyser import analyse_product, analyse_meal, analyse_menu, chat_assistant, chat_emoji, onboarding_assistant, sigmund_assistant, enrich_nutrition
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

@app.after_serving
async def shutdown():
    """Run once when the server is shutting down."""
    await close_pool()

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

@app.route('/api/v1/product/<gtin>', methods=['GET'])
async def get_product(gtin):
    logger.info(f"Scan: {gtin}")

    if not gtin.isdigit() or not (MIN_GTIN_LEN <= len(gtin) <= MAX_GTIN_LEN):
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
            logger.error(f"Product enrichment stream crashed: {str(e)}", exc_info=True)
            yield _sse("error", {"message": "Analytics failed. Please try scanning again."})

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

    logger.info(f"Analyse product: {gtin}")
    log_ocr_scan(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)

    result = await analyse_product(gtin=gtin, text_front=text_front, text_nutrition=text_nutrition)
    if not result:
        logger.error(f"Analysis failed for product {gtin}")
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200

@app.route('/api/v1/analyse/meal', methods=['POST'])
async def analyse_meal_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    image = data.get('image', '')
    profile = data.get('profile', {})

    if isinstance(image, str): image = image.strip()
    if not image:
        return jsonify({"error": "Missing image."}), 400

    logger.info(f"Analyse meal ({len(image)} chars base64)")

    profile_str = json.dumps(profile) if isinstance(profile, dict) else str(profile)

    result = await analyse_meal(image=image, profile=profile_str)
    if not result:
        logger.error("Analysis failed for meal photo")
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200

@app.route('/api/v1/analyse/menu', methods=['POST'])
async def analyse_menu_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    text = data.get('text', '')

    if isinstance(text, str): text = text.strip()
    if not text:
        logger.error("Missing text in menu request")
        return jsonify({"error": "Missing text."}), 400

    saved_file = log_menu_scan(text)
    logger.info(f"Analyse menu ({len(text)} chars) -> Saved to {saved_file}")

    result = await analyse_menu(text=text)
    if not result:
        logger.error("Analysis failed for menu")
        return jsonify({"error": "Analysis failed."}), 500

    return jsonify({"status": "ok", "result": result}), 200

@app.post("/api/v1/chat/stream")
async def chat_stream_endpoint():
    try:
        data = await request.get_json() 
    except Exception as e:
        print(f"❌ Failed to parse incoming JSON: {e}")
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

    logger.info(f"Chat stream request received: {message[:30]}...")

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
            logger.error(f"Stream crashed: {str(e)}", exc_info=True)
            yield _sse("error", {"message": "An unexpected server error occurred."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.route('/api/v1/chat/emoji', methods=['POST'])
async def chat_emoji_route():
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

    logger.info(f"Onboarding stream request received: {message[:30]}...")

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
            logger.error(f"Stream crashed: {str(e)}", exc_info=True)
            yield _sse("error", {"message": "Analysis failed."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.post("/api/v1/chat/sigmund")
async def chat_sigmund_endpoint():
    try:
        data = await request.get_json() 
    except Exception as e:
        print(f"❌ Failed to parse incoming JSON: {e}")
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
            logger.error(f"Sigmund stream crashed: {str(e)}", exc_info=True)
            yield _sse("error", {"message": "An unexpected server error occurred."})

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control':     'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection':        'keep-alive',
    })

@app.route('/api/v1/enrich/nutrition', methods=['POST'])
async def enrich_nutrition_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    gtin = data.get('gtin', '').strip()
    ocr_text = data.get('ocr_text', '').strip()

    if not gtin or not ocr_text:
        return jsonify({"error": "Missing gtin or ocr_text."}), 400

    logger.info(f"Enrich nutrition requested for GTIN: {gtin}")
    
    nutrition_data = await enrich_nutrition(gtin, ocr_text)
    
    if not nutrition_data:
        logger.error(f"Nutrition extraction failed for GTIN: {gtin}")
        return jsonify({"error": "Failed to parse nutrition data."}), 500

    patched = await patch_nutrition(gtin, nutrition_data)
    if patched:
        logger.info(f"Successfully patched nutrition data for GTIN: {gtin} in global database.")
    else:
        logger.warning(f"Could not find GTIN {gtin} in database to patch, returning payload to client anyway.")

    return jsonify(nutrition_data), 200

@app.route('/api/v1/messages', methods=['GET'])
async def messages_route():
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
 
    logger.info(f"Messages: device={device_id[:8]}… context={context} force={force}")
 
    messages = await get_messages(
        device_id   = device_id,
        since       = since,
        context     = context,
        force       = force,
        ip          = ip,
        app_version = app_version,
    )
    return jsonify(messages), 200

if __name__ == '__main__':
    from hypercorn.config import Config
    from hypercorn.asyncio import serve
    import asyncio
    config = Config()
    config.bind = ["127.0.0.1:5000"]
    asyncio.run(serve(app, config))
