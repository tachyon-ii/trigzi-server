#!/usr/bin/env python3
import os
import sys
import glob
import asyncio
import argparse

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.llm.config import config
from core.llm.router import router
from core.llm.skills import SkillsLibrary
from core.llm.validator import SchemaValidator
from core.llm.errors import LLMError

def get_task_payload_and_prompt(task: str, content: str):
    """
    Builds the payload and formats the prompt using strict 1:1 naming conventions.
    The task argument MUST exactly match the prompt filename and the config key.
    """
    # 1. Dynamically load the exact prompt file matching the key
    prompt_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'prompts', f'{task}.txt'))
    if not os.path.exists(prompt_path):
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")
        
    with open(prompt_path, 'r', encoding='utf-8') as f:
        template = f.read()

    # 2. Build the specific payload structure required by the router
    if task == "analyse_menu":
        return {"menu_text": content}, template.format(menu_text=content)
    
    # Add new tasks here as we expand the benchmark suite (e.g., enrich_product)
    raise ValueError(f"Payload builder not implemented for task: '{task}'")

async def evaluate_model(task: str, model: str, fixture_path: str, verbosity: int) -> dict:
    fixture_name = os.path.basename(fixture_path)
    
    with open(fixture_path, 'r', encoding='utf-8') as f:
        content = f.read()

    payload, prompt_text = get_task_payload_and_prompt(task, content)
    
    result_record = {
        "model": model,
        "fixture": fixture_name,
        "latency_ms": -1,
        "schema_pass": False,
        "status": "FAILED",
        "error": None
    }

    if verbosity >= 5:
        print(f"\n▶️  [START] {model} -> {fixture_name}")

    try:
        # Force a direct route using a single-item list
        response = await router.analyze(
            payload=payload,
            profile="",
            model_strings=[model],
            optimize="direct",
            timeout=30.0
        )
        
        raw_text = response.get("result", "")
        latency = response.get("latency_ms", -1)
        
        # Run the rigid schema validator
        schema_valid = SchemaValidator.validate_stream(prompt_text, raw_text)
        
        result_record["latency_ms"] = latency
        result_record["schema_pass"] = schema_valid
        result_record["status"] = "OK"

        if verbosity >= 9:
            print(f"\n{'='*40}\n[RAW OUTPUT: {model} | {latency}ms]\n{'='*40}")
            print(raw_text)
            print(f"{'='*40}\n")
            
    except LLMError as e:
        result_record["error"] = type(e).__name__
    except Exception as e:
        result_record["error"] = str(e)

    if verbosity >= 5:
        icon = "✅" if result_record["schema_pass"] else "❌"
        print(f"{icon} [DONE] {model} -> {fixture_name} ({result_record['latency_ms']}ms)")

    return result_record

async def main():
    parser = argparse.ArgumentParser(description="Trigzi LLM Benchmark Rig")
    parser.add_argument("task", help="The routing task name (e.g., menu_extract)")
    parser.add_argument("-v", "--verbosity", type=int, default=9, help="Noise level (0-9)")
    args = parser.parse_args()

    print(f"🚀 Initializing Benchmark Pipeline for task: {args.task}")
    
    # 1. Load the target models directly from your central config
    task_cfg = config.task_config(args.task)
    target_models = task_cfg.get("models", [])
    
    if not target_models:
        print(f"❌ No models configured for task '{args.task}' in llm_providers.json.")
        sys.exit(1)
        
    print(f"📡 Target Models: {', '.join(target_models)}")

    # 2. Discover fixtures
    fixtures_dir = os.path.join(os.path.dirname(__file__), '..', 'eval', 'fixtures', args.task)
    fixtures = glob.glob(os.path.join(fixtures_dir, '*.txt'))
    
    if not fixtures:
        print(f"❌ No .txt fixtures found in {fixtures_dir}")
        sys.exit(1)
        
    print(f"📁 Found {len(fixtures)} fixtures. Beginning evaluation...\n")

    # 3. Execute the matrix
    tasks = []
    for model in target_models:
        for fixture in fixtures:
            tasks.append(evaluate_model(args.task, model, fixture, args.verbosity))
            
    results = await asyncio.gather(*tasks)

    # 4. Print the clean summary
    print("\n\n" + "="*80)
    print(f"📊 BENCHMARK SUMMARY: {args.task.upper()}")
    print("="*80)
    print(f"{'MODEL':<25} | {'FIXTURE':<20} | {'LATENCY':<8} | {'SCHEMA':<6} | {'STATUS'}")
    print("-" * 80)
    
    # Sort results by fixture, then latency
    results.sort(key=lambda x: (x['fixture'], x['latency_ms']))
    
    for r in results:
        lat_str = f"{r['latency_ms']/1000:.2f}s" if r['latency_ms'] > 0 else "---"
        sch_str = "PASS" if r['schema_pass'] else "FAIL"
        err_str = r['error'] if r['error'] else r['status']
        print(f"{r['model']:<25} | {r['fixture']:<20} | {lat_str:<8} | {sch_str:<6} | {err_str}")
    
    print("="*80 + "\n")

if __name__ == '__main__':
    asyncio.run(main())
