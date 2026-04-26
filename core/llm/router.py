#!/usr/bin/env python3
"""
=============================================================================
Module:        LLM Router
Location:      core/llm/router.py
Description:   Central dispatch and execution engine for all LLM API calls.
               Decouples the application layer from specific providers (OpenAI,
               Claude, Gemini) and manages dynamic execution strategies.

Execution Modes:
- direct:   Standard 1:1 call to a specific model.
- failover: Sequential fallback if the primary provider fails.
- race:     Fires multiple providers concurrently, returning the fastest.
- ab:       Fires a primary model returning to the user, while silently
           firing secondary models in the background for data gathering.
- cost:     Dynamically selects the cheapest capable model.

Architecture Note:
This layer acts as a 'dumb pipe'. It does not parse or validate
LLM outputs (that is delegated to SchemaValidator). It strictly
handles HTTP transport, latency tracking, and non-blocking I/O
telemetry logging via asyncio.to_thread.
=============================================================================
"""

# pylint: disable=duplicate-code
# Justification: shares the response-metadata header format with
# scripts/llm_push.py (PROVIDER/MODEL/LATENCY/FALLBACK lines). Both
# write the same shape because they consume the same response dict;
# extracting a helper would force scripts/ to depend on a private
# core.llm.router serialisation function for what amounts to six
# format strings. Accepting the duplicate.

from __future__ import annotations

import asyncio
import os
import uuid
import random
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from .errors import LLMError
from .config import config
from .providers.gemini import GeminiProvider
from .providers.claude import ClaudeProvider
from .providers.openai import OpenAIProvider

_RESPONSES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'logs', 'llm_responses')


class LLMRouter:  # pylint: disable=too-few-public-methods
    """Singleton dispatcher for all LLM API calls.

    Holds a registry of provider clients and exposes a single public entry
    point, ``analyse``, which selects an execution strategy (direct / race /
    failover / ab / cost) based on the caller's ``optimize`` parameter and
    fans out to the matching ``_execute_*`` private method.

    The single-public-method shape is intentional: the router is a facade,
    and the ``_execute_*`` methods are deliberately private to discourage
    callers from bypassing the strategy-selection logic in ``analyse``.
    """

    def __init__(self):
        self.registry = {
            "gemini": GeminiProvider(),
            "claude": ClaudeProvider(),
            "openai": OpenAIProvider()
        }
        self._latency_cache: Dict[str, Dict[str, Any]] = {}

    async def analyse(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        payload: Dict[str, Any],
        profile: str,
        model_strings: List[str],
        optimize: str = "accuracy",
        timeout: float = 30.0,
        expected_keys: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Dispatch a single LLM call using the strategy implied by ``optimize``.

        Resolves ``model_strings`` + ``optimize`` to one of five execution
        modes (direct / race / failover / ab / cost) and delegates to the
        matching ``_execute_*`` method. Raises :class:`LLMError` if no
        providers are configured or if the resolved mode is unknown.
        """
        if not model_strings:
            raise LLMError.no_providers_configured()

        mode       = self._resolve_mode(model_strings, optimize)
        session_id = str(uuid.uuid4())

        if mode == "direct":
            return await self._execute_direct(payload, profile, model_strings[0], session_id, timeout, expected_keys)
        if mode == "race":
            return await self._execute_race(payload, profile, model_strings, session_id, timeout, expected_keys)
        if mode == "failover":
            return await self._execute_failover(payload, profile, model_strings, session_id, timeout, expected_keys)
        if mode == "ab":
            return await self._execute_ab(payload, profile, model_strings, session_id, timeout, expected_keys)
        if mode == "cost":
            return await self._execute_cost(payload, profile, model_strings, session_id, timeout, expected_keys)

        raise LLMError.invalid_request(f"Unknown routing mode: {mode}")

    def _resolve_mode(self, models: List[str], optimize: str) -> str:
        if len(models) == 1:
            return "direct"
        if optimize == "speed":
            return "race"
        if optimize == "accuracy":
            return "ab"
        if optimize == "cost":
            return "cost"
        return "failover"

    async def _execute_direct(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, payload, profile, model_str, session_id, timeout, expected_keys=None
    ) -> Dict[str, Any]:
        provider_key, model_tag = self._parse_model_string(model_str)
        provider = self.registry.get(provider_key)

        if not provider:
            raise LLMError.unknown_provider(provider_key)

        resolved_tag = config.resolve(model_tag, provider_key)
        logging.info("📍 Routing request to %s (%s)...", provider_key.upper(), resolved_tag)

        try:
            response = await provider.analyse(payload, profile, resolved_tag, timeout, expected_keys)
            response['payload'] = payload
            self._record_call(response, session_id, success=True)
            self._update_latency(resolved_tag, response["latency_ms"])

            logging.info("✅ %s succeeded in %dms", provider_key.upper(), response['latency_ms'])
            return response

        except BaseException as e:
            logging.error("❌ %s halted: %s - %s", provider_key.upper(), type(e).__name__, str(e))
            fail_resp = {
                'provider': provider_key,
                'model': resolved_tag,
                'latency_ms': -1,
                'payload': payload,
                'raw_json': f"CRASH/TIMEOUT/CANCELLED: {type(e).__name__} - {str(e)}"
            }
            self._record_call(fail_resp, session_id, success=False)
            raise e

    async def _execute_race(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, payload, profile, models, session_id, timeout, expected_keys=None
    ) -> Dict[str, Any]:
        tasks = [
            asyncio.create_task(self._execute_direct(payload, profile, m, session_id, timeout, expected_keys))
            for m in models
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for p in pending:
            p.cancel()

        for task in done:
            try:
                return task.result()
            except Exception as e:
                logging.warning("⚠️ Racer failed: %s", e)

        raise LLMError.all_providers_failed(models)

    async def _execute_failover(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, payload, profile, models, session_id, timeout, expected_keys=None
    ) -> Dict[str, Any]:
        is_fallback = False

        for m in models:
            try:
                response = await self._execute_direct(payload, profile, m, session_id, timeout, expected_keys)
                if is_fallback:
                    response["was_fallback"] = True
                    logging.info("⚡ Failover succeeded via %s", m)
                return response
            except LLMError as e:
                if not e.is_failoverable:
                    raise e
                logging.warning("⚠️ [%s] failed (%s). Trying next...", m, e)
                is_fallback = True

        raise LLMError.all_providers_failed(models)

    async def _execute_ab(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, payload, profile, models, session_id, timeout, expected_keys=None
    ) -> Dict[str, Any]:
        shuffled = list(models)
        random.shuffle(shuffled)
        primary = shuffled[0]
        others  = shuffled[1:]

        response = await self._execute_direct(payload, profile, primary, session_id, timeout, expected_keys)

        for m in others:
            asyncio.create_task(self._execute_direct_silent(payload, profile, m, session_id, timeout, expected_keys))

        return response

    async def _execute_cost(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, payload, profile, models, session_id, timeout, expected_keys=None
    ) -> Dict[str, Any]:
        best_model_str = None
        best_cost      = float("inf")

        for model_str in models:
            provider_key, _ = self._parse_model_string(model_str)
            cheapest_tag    = config.cheapest_model(provider_key)
            if cheapest_tag is None:
                continue

            cost = config.estimate_cost(cheapest_tag, input_tokens=0, output_tokens=1_000_000)
            if cost is not None and cost < best_cost:
                best_cost      = cost
                best_model_str = cheapest_tag

        if best_model_str is None:
            best_model_str = models[0]

        logging.info("💰 cost mode: selected %s ($%.4f/1M output tokens)", best_model_str, best_cost)
        return await self._execute_direct(payload, profile, best_model_str, session_id, timeout, expected_keys)

    async def _execute_direct_silent(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, payload, profile, model_str, session_id, timeout, expected_keys=None
    ):
        try:
            await self._execute_direct(payload, profile, model_str, session_id, timeout, expected_keys)
        except Exception:
            pass

    def _parse_model_string(self, s: str):
        s = s.lower().strip()
        if s == "gemini" or s.startswith("gemini-") or s.startswith("gemma-"):
            return "gemini", s
        if s == "claude" or s.startswith("claude-"):
            return "claude", s
        if s in ("openai", "gpt-4o") or s.startswith("gpt-") or s.startswith("o1") or s.startswith("o3") or s.startswith("o4"):
            return "openai", s
        return "unknown", s

    def _record_call_sync(self, response: Dict[str, Any], session_id: str, success: bool):
        timestamp = datetime.now()
        ts_human  = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
        ts_file   = timestamp.strftime('%Y%m%d%H%M%S')
        status    = "SUCCESS" if success else "FAILED"

        try:
            payload = response.get('payload', {})
            gtin    = (payload.get('product', {}) or {}).get('gtin', '') \
                      or payload.get('gtin', '') \
                      or 'unknown'
            raw     = response.get('raw_json', '')

            os.makedirs(_RESPONSES_DIR, exist_ok=True)
            filename = f"{ts_file}_{status}_{gtin}.txt"

            with open(os.path.join(_RESPONSES_DIR, filename), 'w', encoding='utf-8') as f:
                f.write(f"# TIMESTAMP: {ts_human}\n")
                f.write(f"# STATUS:    {status}\n")
                f.write(f"# SESSION:   {session_id}\n")
                f.write(f"# PROVIDER:  {response.get('provider', 'Unknown')}\n")
                f.write(f"# MODEL:     {response.get('model', 'Unknown')}\n")
                f.write(f"# LATENCY:   {response.get('latency_ms', -1)}ms\n")
                f.write(f"# FALLBACK:  {response.get('was_fallback', False)}\n")
                f.write(f"# GTIN:      {gtin}\n")
                f.write("#\n")
                f.write(str(raw))
        except Exception as e:
            logging.error("llm_responses write failed: %s", e)

    def _record_call(self, response: Dict[str, Any], session_id: str, success: bool):
        asyncio.create_task(asyncio.to_thread(self._record_call_sync, response, session_id, success))

    def _update_latency(self, model_tag: str, latency_ms: int):
        if model_tag not in self._latency_cache:
            self._latency_cache[model_tag] = {"avg": float(latency_ms), "count": 1}
        else:
            c = self._latency_cache[model_tag]
            c["avg"]   = 0.25 * latency_ms + 0.75 * c["avg"]
            c["count"] += 1

router = LLMRouter()
