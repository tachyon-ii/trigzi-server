# core/llm/router.py
import asyncio
import uuid
import random
from datetime import datetime
from typing import List, Dict, Any

from .errors import LLMError
from .config import config
from .providers.gemini import GeminiProvider
from .providers.claude import ClaudeProvider
from .providers.openai import OpenAIProvider


class LLMRouter:
    """
    Central hub for LLM request orchestration.
    Ported from LLMRouter.swift.

    Modes:
    - direct:   Single model, straight through
    - race:     Concurrent, return fastest
    - failover: Sequential, error-based fallback chain
    - ab:       Primary returns to caller; others fire in background for telemetry
    """

    def __init__(self):
        self.registry = {
            "gemini": GeminiProvider(),
            "claude": ClaudeProvider(),
            "openai": OpenAIProvider()
        }
        # EMA latency cache: {model_tag: {avg: float, count: int}}
        self._latency_cache: Dict[str, Dict[str, Any]] = {}

    # MARK: - Public API

    async def analyze(
        self,
        payload: Dict[str, Any],
        profile: str,
        model_strings: List[str],
        optimize: str = "accuracy",
        timeout: float = 30.0
    ) -> Dict[str, Any]:

        if not model_strings:
            raise LLMError.no_providers_configured()

        mode       = self._resolve_mode(model_strings, optimize)
        session_id = str(uuid.uuid4())

        if mode == "direct":
            return await self._execute_direct(payload, profile, model_strings[0], session_id, timeout)
        elif mode == "race":
            return await self._execute_race(payload, profile, model_strings, session_id, timeout)
        elif mode == "failover":
            return await self._execute_failover(payload, profile, model_strings, session_id, timeout)
        elif mode == "ab":
            return await self._execute_ab(payload, profile, model_strings, session_id, timeout)
        elif mode == "cost":
            return await self._execute_cost(payload, profile, model_strings, session_id, timeout)

        raise LLMError.invalid_request(f"Unknown routing mode: {mode}")

    # MARK: - Mode resolution

    def _resolve_mode(self, models: List[str], optimize: str) -> str:
        if len(models) == 1:
            return "direct"
        if optimize == "speed":
            return "race"
        if optimize == "accuracy":
            return "ab"
        if optimize == "cost":
            return "cost"
        return "failover"   # default: covers "failover" explicitly and any unrecognised value

    # MARK: - Execution engines

    async def _execute_direct(self, payload, profile, model_str, session_id, timeout) -> Dict[str, Any]:
        provider_key, model_tag = self._parse_model_string(model_str)
        provider = self.registry.get(provider_key)

        if not provider:
            raise LLMError.unknown_provider(provider_key)

        resolved_tag = config.resolve(model_tag, provider_key)
        response     = await provider.analyze(payload, profile, resolved_tag, timeout)

        self._record_call(response, session_id, success=True)
        self._update_latency(resolved_tag, response["latency_ms"])

        return response

    async def _execute_race(self, payload, profile, models, session_id, timeout) -> Dict[str, Any]:
        tasks = [
            asyncio.create_task(self._execute_direct(payload, profile, m, session_id, timeout))
            for m in models
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for p in pending:
            p.cancel()

        for task in done:
            try:
                winner = task.result()
                print(f"🏁 Race won by {winner['provider']}/{winner['model']} in {winner['latency_ms']}ms")
                return winner
            except Exception as e:
                print(f"⚠️ Racer failed: {e}")

        raise LLMError.all_providers_failed(models)

    async def _execute_failover(self, payload, profile, models, session_id, timeout) -> Dict[str, Any]:
        is_fallback = False

        for m in models:
            try:
                response = await self._execute_direct(payload, profile, m, session_id, timeout)
                if is_fallback:
                    response["was_fallback"] = True
                    print(f"⚡️ Failover succeeded via {m}")
                return response
            except LLMError as e:
                if not e.is_failoverable:
                    raise e
                print(f"❌ [{m}] failed ({e}). Trying next...")
                is_fallback = True

        raise LLMError.all_providers_failed(models)

    async def _execute_ab(self, payload, profile, models, session_id, timeout) -> Dict[str, Any]:
        shuffled = list(models)
        random.shuffle(shuffled)

        primary = shuffled[0]
        others  = shuffled[1:]

        # Primary is blocking — returned to caller
        response = await self._execute_direct(payload, profile, primary, session_id, timeout)

        # Others fire in background for telemetry / consensus
        for m in others:
            asyncio.create_task(self._execute_direct_silent(payload, profile, m, session_id, timeout))

        return response

    async def _execute_cost(self, payload, profile, models, session_id, timeout) -> Dict[str, Any]:
        """
        Cost mode: select the single cheapest model across all requested
        providers using output_per_million rates from llm_providers.json,
        then execute as a direct call.
        """
        best_model_str = None
        best_cost      = float("inf")

        for model_str in models:
            provider_key, _ = self._parse_model_string(model_str)
            cheapest_tag    = config.cheapest_model(provider_key)
            if cheapest_tag is None:
                continue
            # Normalised comparison: cost at 1M output tokens
            cost = config.estimate_cost(cheapest_tag, input_tokens=0, output_tokens=1_000_000)
            if cost is not None and cost < best_cost:
                best_cost      = cost
                best_model_str = cheapest_tag

        if best_model_str is None:
            # No costed models found — fall back to first model direct
            print(f"⚠️  cost mode: no costed models found, falling back to {models[0]}")
            best_model_str = models[0]

        print(f"💰 cost mode: selected {best_model_str} (${best_cost:.4f}/1M output tokens)")
        return await self._execute_direct(payload, profile, best_model_str, session_id, timeout)

    async def _execute_direct_silent(self, payload, profile, model_str, session_id, timeout):
        try:
            await self._execute_direct(payload, profile, model_str, session_id, timeout)
        except Exception:
            pass

    # MARK: - Helpers

    def _parse_model_string(self, s: str):
        """Maps model string to (provider_key, model_tag). Matches Swift LLMModel.init(string:)."""
        s = s.lower().strip()
        if s == "gemini" or s.startswith("gemini-"):       return "gemini", s
        if s == "claude" or s.startswith("claude-"):       return "claude", s
        if s in ("openai", "gpt-4o") or s.startswith("gpt-") \
                or s.startswith("o1") or s.startswith("o3") \
                or s.startswith("o4"):                      return "openai", s
        return "unknown", s

    def _record_call(self, response: Dict[str, Any], session_id: str, success: bool):
        timestamp = datetime.now().isoformat()
        print(
            f"📝 [LOG] {timestamp} | session={session_id} | "
            f"provider={response['provider']} | model={response['model']} | "
            f"latency={response['latency_ms']}ms | fallback={response.get('was_fallback', False)}"
        )

    def _update_latency(self, model_tag: str, latency_ms: int):
        if model_tag not in self._latency_cache:
            self._latency_cache[model_tag] = {"avg": float(latency_ms), "count": 1}
        else:
            c = self._latency_cache[model_tag]
            c["avg"]   = 0.25 * latency_ms + 0.75 * c["avg"]
            c["count"] += 1


# Singleton for Flask app
router = LLMRouter()
