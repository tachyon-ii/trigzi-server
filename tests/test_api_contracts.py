"""
=============================================================================
Module:        Test — API Contract Validator (Live)
Location:      tests/test_api_contracts.py
Description:   Dynamic API contract validator. Reads every .json schema
               in tests/schemas/endpoints/ and validates that live
               server responses conform to the declared shape. Used
               for end-to-end contract verification against a running
               trigzi backend; not part of the unit test suite.

Architecture Note:
A lightweight unittest stub (TestAPIContractsPlaceholder) keeps
pytest discovery happy when this file gets collected, but the real
work happens via direct invocation against a running server. The
schemas live under tests/schemas/endpoints/ as one .json per
endpoint, each declaring its path, test payload, and expected
response schema (jsonschema dialect).
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument

import asyncio
import glob
import json
import os
import sys
import unittest

import aiohttp
from jsonschema import validate, ValidationError


class TestAPIContractsPlaceholder(unittest.TestCase):
    def test_placeholder_for_pytest(self):
        """Dummy test to prevent pytest from failing when it collects this file."""
        # The real validation runs against a live server; this stub just
        # confirms the module imports cleanly during pytest collection.
        self.assertEqual(1, 1, "Loaded ok! Run this directly to hit the live server.")

BASE_URL = "http://127.0.0.1:5000"
SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas", "endpoints")

# 🛡️ THE FIX: New validator for standard REST JSON endpoints
async def validate_json_endpoint(session: aiohttp.ClientSession, spec: dict, filepath: str) -> bool:
    path = spec.get("path")
    payload = spec.get("test_payload", {})
    schema = spec.get("response_schema", {})

    try:
        async with session.post(f"{BASE_URL}{path}", json=payload) as resp:
            if resp.status != 200:
                print(f"  ❌ HTTP ERROR: Returned {resp.status}")
                return False

            try:
                data_json = await resp.json()
            except json.JSONDecodeError:
                print("  ❌ JSON DECODE ERROR: Could not parse response body.")
                return False

            # === SCHEMA VALIDATION ===
            try:
                validate(instance=data_json, schema=schema)
                print("  ✅ PASS: API Contract perfectly honored.")
                return True
            except ValidationError as e:
                print("  ❌ SCHEMA VIOLATION:")
                print(f"     Expected: {e.schema}")
                print(f"     Received: {data_json}")
                print(f"     Reason:   {e.message}")
                return False

    except Exception as e:
        print(f"  ❌ FATAL EXCEPTION: {str(e)}")
        return False

async def validate_sse_endpoint(session: aiohttp.ClientSession, spec: dict, filepath: str) -> bool:
    has_errors = False
    path = spec.get("path")
    payload = spec.get("test_payload", {})
    schemas = spec.get("sse_schemas", {})

    try:
        async with session.post(f"{BASE_URL}{path}", json=payload) as resp:
            if resp.status != 200:
                print(f"  ❌ HTTP ERROR: Returned {resp.status}")
                return False

            current_event = None

            async for line in resp.content:
                line = line.decode('utf-8').strip()

                if line.startswith("event: "):
                    current_event = line[7:].strip()

                elif line.startswith("data: "):
                    data_str = line[6:].strip()
                    if not current_event:
                        print("  ❌ PROTOCOL ERROR: Received 'data:' without preceding 'event:'")
                        has_errors = True
                        continue

                    try:
                        data_json = json.loads(data_str)
                    except json.JSONDecodeError:
                        print(f"  ❌ JSON DECODE ERROR: Could not parse chunk: {data_str}")
                        has_errors = True
                        continue

                    schema = schemas.get(current_event)
                    if not schema:
                        print(f"  ❌ UNKNOWN EVENT TYPE: '{current_event}' has no schema defined in {os.path.basename(filepath)}.")
                        has_errors = True
                        continue

                    # === SCHEMA VALIDATION ===
                    try:
                        validate(instance=data_json, schema=schema)
                    except ValidationError as e:
                        print(f"  ❌ SCHEMA VIOLATION in event '{current_event}':")
                        print(f"     Expected: {e.schema}")
                        print(f"     Received: {data_json}")
                        print(f"     Reason:   {e.message}")
                        has_errors = True
                        continue

                    # Schema Leak Check
                    if current_event == "text":
                        content = data_json.get("content", "").lower()
                        if "message:" in content or "response:" in content or "\naction:" in content:
                            print(f"  ❌ LEAK ERROR: Prompt schema leaked into UI payload: {data_json['content']}")
                            has_errors = True

    except Exception as e:
        print(f"  ❌ FATAL EXCEPTION: {str(e)}")
        return False

    if not has_errors:
        print("  ✅ PASS: API Contract perfectly honored.")
        return True
    return False

async def main():
    schema_files = glob.glob(os.path.join(SCHEMA_DIR, "*.json"))
    if not schema_files:
        print(f"❌ No schema files found in {SCHEMA_DIR}")
        sys.exit(1)

    print(f"🚀 Found {len(schema_files)} endpoint schemas. Running tests against {BASE_URL}...\n")

    timeout = aiohttp.ClientTimeout(total=60)
    passed = 0

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for filepath in schema_files:
            with open(filepath, 'r', encoding='utf-8') as f:
                spec = json.load(f)

            print("-" * 60)
            print(f"Testing: {spec.get('description', 'Unknown')} ({spec.get('path')})")

            # 🛡️ THE FIX: Route the test based on the response type
            if spec.get("response_type") == "json":
                success = await validate_json_endpoint(session, spec, filepath)
            elif spec.get("response_type") == "sse":
                success = await validate_sse_endpoint(session, spec, filepath)
            else:
                print(f"  ⚠️ Unsupported response_type: {spec.get('response_type')}")
                success = False

            if success:
                passed += 1

    print("\n" + "="*60)
    print(f"RESULTS: {passed}/{len(schema_files)} Endpoints Passed Schema Validation.")
    if passed < len(schema_files):
        print("🚨 CONTRACT VIOLATION DETECTED. FIX APP.PY BEFORE DEPLOYING.")
        sys.exit(1)
    else:
        print("🎉 ALL SYSTEMS GO.")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
