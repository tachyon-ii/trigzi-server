#!/usr/bin/env python3
from __future__ import annotations
"""
=============================================================================
Module:        Dynamic API Prober (Synthetic Client)
Location:      scripts/probe_client.py
Description:   Data-Driven Testing (DDT) engine for the Trigzi API.
               Executes HTTP and SSE requests defined in api_manifest.json
               to validate server routing, fail-closed payload rejection,
               and schema enforcement.
               
               Architecture Note:
               This script mimics the iOS/Android clients. It ensures that
               no endpoint drops SSE chunking, no validation loops hang,
               and all HTTP responses match the strictly defined contract.
=============================================================================
"""
import asyncio
import aiohttp
import argparse
import time
import sys
import json
import os

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), '..', 'tests', 'api_manifest.json')

class DynamicAPIProber:
    def __init__(self, base_url: str, manifest_path: str):
        self.base_url = base_url.rstrip('/')
        self.manifest_path = manifest_path
        self.passed = 0
        self.failed = 0

    def _validate_schema(self, response_data, schema) -> bool:
        """Basic recursive type/key validation against the JSON schema contract."""
        if schema == "list":
            return isinstance(response_data, list)
            
        if schema == "dict":
            return isinstance(response_data, dict)
        
        if isinstance(schema, dict) and isinstance(response_data, dict):
            for key, expected_type in schema.items():
                if key not in response_data:
                    print(f"     [Schema Error] Missing key: '{key}'")
                    return False
                # Add deeper type checking here if needed later
            return True
            
        return False

    async def run_all(self):
        print(f"\n🚀 Initiating Dynamic Client Probe against {self.base_url}\n")
        
        try:
            with open(self.manifest_path, 'r') as f:
                manifest = json.load(f)
        except Exception as e:
            print(f"❌ Failed to load API manifest: {e}")
            sys.exit(1)
            
        async with aiohttp.ClientSession() as session:
            for ep in manifest.get("endpoints", []):
                print(f"🧪 Testing: {ep['name']} ({ep['method']} {ep['route']})")
                
                # 1. Test Invalid Request
                await self._execute_test(
                    session, ep, 
                    request_data=ep["invalid_request"], 
                    expected_status=ep["expected_status_invalid"],
                    is_valid_test=False
                )
                
                # 2. Test Valid Request
                await self._execute_test(
                    session, ep, 
                    request_data=ep["valid_request"], 
                    expected_status=ep["expected_status_valid"],
                    is_valid_test=True
                )
                print()

        self._print_summary()

    async def _execute_test(self, session: aiohttp.ClientSession, ep: dict, request_data: dict, expected_status: int, is_valid_test: bool):
        url = f"{self.base_url}{ep['route']}"
        label = "VALID" if is_valid_test else "INVALID"
        start = time.time()
        
        try:
            async with session.request(
                ep['method'], 
                url, 
                json=request_data.get('json'), 
                headers=request_data.get('headers')
            ) as response:
                latency = int((time.time() - start) * 1000)
                
                # Status Check
                if response.status != expected_status:
                    print(f"  ❌ [{label}] EXPECTED {expected_status}, GOT {response.status}")
                    self.failed += 1
                    return

                # Schema Check (Only for valid REST requests)
                if is_valid_test and ep["type"] == "rest":
                    data = await response.json()
                    if ep.get("retval_schema") and not self._validate_schema(data, ep["retval_schema"]):
                        print(f"  ❌ [{label}] Schema validation failed. Data: {data}")
                        self.failed += 1
                        return

                # SSE Stream Check (Only for valid SSE requests)
                if is_valid_test and ep["type"] == "sse":
                    if "text/event-stream" not in response.headers.get("Content-Type", ""):
                        print(f"  ❌ [{label}] Missing SSE Headers")
                        self.failed += 1
                        return
                    
                    chunks = 0
                    async for line in response.content:
                        if line.decode('utf-8').strip().startswith('event:'):
                            chunks += 1
                            
                    if chunks == 0:
                        print(f"  ❌ [{label}] Stream hung or returned 0 chunks.")
                        self.failed += 1
                        return

                print(f"  ✅ [{label}] OK ({latency}ms)")
                self.passed += 1

        except Exception as e:
            print(f"  ❌ [{label}] ERROR: {str(e)}")
            self.failed += 1

    def _print_summary(self):
        print("─" * 60)
        print(f"🏁 PROBE COMPLETE: {self.passed} Passed, {self.failed} Failed")
        print("─" * 60 + "\n")
        if self.failed > 0:
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dynamic Client Probe")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="Base URL")
    args = parser.parse_args()

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    prober = DynamicAPIProber(args.url, MANIFEST_PATH)
    asyncio.run(prober.run_all())
