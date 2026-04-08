#!/usr/bin/env python3
import asyncio
import argparse
import json
import os
import sys
import time
from pathlib import Path
import uuid

# 1. Bootstrap the Python path BEFORE importing local modules.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.llm.router import LLMRouter 
from core.llm.skills import SkillsLibrary

llm_router = LLMRouter()

# --- Configuration & Paths ---
PROVIDERS_FILE = BASE_DIR / "core" / "llm" / "llm_providers.json"
FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"

def load_providers():
    if not PROVIDERS_FILE.exists():
        print(f"❌ Error: Providers config not found at {PROVIDERS_FILE}")
        sys.exit(1)
    with open(PROVIDERS_FILE, 'r') as f:
        providers = json.load(f)
    print(f"✅ LLMProviderConfig: loaded {len(providers)} providers from {PROVIDERS_FILE.name}")
    return providers

def get_task_payload_and_prompt(task, content):
    """
    Router for building the strict API payload based on the task name.
    """
    if task == "chat_assistant":
        data = json.loads(content)
        ctx_str = (
            f"Dietary Profile: {json.dumps(data.get('system_context', {}).get('dietary_profile', {}))}\n"
            f"Active Menu Context: {json.dumps(data.get('system_context', {}).get('current_menu', []))}"
        )
        hist_str = ""
        for msg in data.get("history", []):
            role = "User" if msg.get("role") == "user" else "Trigzi"
            hist_str += f"{role}: {msg.get('content', '')}\n"

        prompt = SkillsLibrary.chat_assistant_prompt(
            system_context=ctx_str,
            history=hist_str,
            message=data.get("message", "")
        )
        return {"prompt": prompt}, prompt

    elif task == "chat_emoji":
        # For the emoji micro-inference, the fixture is just the raw text string to analyze
        prompt = SkillsLibrary.chat_emoji_prompt(text=content.strip())
        return {"prompt": prompt}, prompt

    elif task == "enrich_product":
        system_prompt = (
            "You are a strictly clinical dietary analysis engine. "
            "Analyze the provided product JSON for hidden allergens and dietary risks. "
            "Output your analysis strictly in flat JSON format."
        )
        user_prompt = f"[PRODUCT DATA]\n{content}\n\n[OUTPUT FORMAT]\nReturn strictly JSON."
        return {"prompt": f"{system_prompt}\n\n{user_prompt}"}, user_prompt

    elif task == "chat_sigmund":
        data = json.loads(content)
        
        ctx_str = (
            f"Dietary Profile: {json.dumps(data.get('system_context', {}).get('dietary_profile', {}))}\n"
            f"Active Menu Context: {json.dumps(data.get('system_context', {}).get('current_menu', []))}"
        )
        
        hist_str = ""
        for msg in data.get("history", []):
            role = "User" if msg.get("role") == "user" else "Trigzi"
            hist_str += f"{role}: {msg.get('content', '')}\n"

        prompt = SkillsLibrary.sigmund_assistant_prompt(
            system_context=ctx_str,
            history=hist_str,
            message=data.get("message", "")
        )
        return {"prompt": prompt}, prompt

    elif task == "enrich_nutrition":
        # The content is just the raw OCR text from the fixture file
        prompt = SkillsLibrary.enrich_nutrition_prompt(ocr_text=content.strip())
        # The payload format expects the {"prompt": ...} wrapper for the RequestFilters
        return {"prompt": prompt}, prompt

    raise ValueError(f"Payload builder not implemented for task: '{task}'")

async def execute_llm_request(model_name, payload):
    """Executes a strict, 1:1 API call via the core router."""
    session_id = f"bench-{uuid.uuid4().hex[:8]}"
    try:
        result = await llm_router._execute_direct(
            payload=payload,
            profile="benchmark", 
            model_str=model_name,
            session_id=session_id,
            timeout=45.0
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"❌ Router Exception: {str(e)}"

async def evaluate_model(model_name, task, content):
    """Builds the payload and executes the network request for a single model."""
    start_time = time.time()
    try:
        payload, prompt_text = get_task_payload_and_prompt(task, content)
        print(f"📡 Sending request to {model_name}...")
        
        response = await execute_llm_request(model_name, payload)
        elapsed = time.time() - start_time
        return {
            "model": model_name,
            "status": "success",
            "time": round(elapsed, 2),
            "response": response
        }
    except Exception as e:
        elapsed = time.time() - start_time
        return {
            "model": model_name,
            "status": "error",
            "time": round(elapsed, 2),
            "error": str(e)
        }

async def main():
    parser = argparse.ArgumentParser(description="Run LLM benchmarking tasks.")
    parser.add_argument("task", help="Task name mapping to llm_providers.json (e.g., chat_assistant, chat_emoji)")
    parser.add_argument("-f", "--fixture", help="Explicit path to a JSON/text fixture file")
    parser.add_argument("-m", "--models", help="Comma-separated list of models to test (overrides config)")
    args = parser.parse_args()

    providers = load_providers()

    # Determine Target Models
    if args.models:
        target_models = [m.strip() for m in args.models.split(",")]
        print("⚠️  Overriding config with CLI model selection.")
    else:
        routing_config = providers.get("routing", {}).get(args.task, {})
        target_models = routing_config.get("models", [])
        if not target_models:
            print(f"❌ CRITICAL ERROR: No 'models' array found for task '{args.task}' in llm_providers.json.")
            sys.exit(1)

    print(f"🚀 Initializing Benchmark Pipeline for task: {args.task}")
    print(f"📡 Target Models: {', '.join(target_models)}")

    # Determine Fixture Path
    if args.fixture:
        fixture_path = Path(args.fixture)
    else:
        # Default convention: tests/fixtures/<task_name>_eval.json
        fixture_path = FIXTURES_DIR / f"{args.task}_eval.json"
        
        # Fallback for older naming conventions (like truffle_sauce) if needed
        if not fixture_path.exists() and args.task == "enrich_product":
            fixture_path = FIXTURES_DIR / "truffle_sauce.json"

    if not fixture_path.exists():
        print(f"❌ CRITICAL ERROR: Fixture file not found at {fixture_path}")
        print(f"Pass a specific file using the -f flag: ./benchmark_task.py {args.task} -f path/to/file.json")
        sys.exit(1)

    print(f"📁 Loaded fixture: {fixture_path.name}\n")

    with open(fixture_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # Fan out concurrently
    tasks = [evaluate_model(model, args.task, content) for model in target_models]
    results = await asyncio.gather(*tasks)

    # Output
    print("-" * 50)
    print("🏆 BENCHMARK RESULTS")
    print("-" * 50)
    for res in results:
        model = res['model']
        if res['status'] == 'success':
            print(f"🟢 {model} ({res['time']}s):\n{res['response']}\n")
        else:
            print(f"🔴 {model} ({res['time']}s) FAILED:\n{res['error']}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
