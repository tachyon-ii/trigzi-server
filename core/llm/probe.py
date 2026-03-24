# core/llm/probe.py
"""
Provider health-check and introspection layer.

Implements probe() for each provider — a lightweight operation that:
  1. Hits the provider's model-list endpoint to confirm reachability
  2. Measures round-trip latency
  3. Returns the list of available models
  4. Validates that the configured default model is in that list
  5. Reports remaining API credit where available via response headers

Credit remaining is also updated on every normal analyze() call via
BaseProvider._update_credit_from_headers() — probe() initialises the
field but does not block on it.

ProbeScheduler runs probe() for all registered providers:
  - Once on startup (before the first request is served)
  - Periodically on a configurable interval (default: 5 minutes)

Usage:
    from core.llm.probe import scheduler

    await scheduler.start()            # call once at Flask app startup
    status = scheduler.status("gemini")
    all    = scheduler.all_status()
"""

import asyncio
import time
import aiohttp
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


# MARK: - ProviderStatus

@dataclass
class ProviderStatus:
    """
    Snapshot of a provider's health at a point in time.
    Produced by probe() and cached by ProbeScheduler.
    """
    provider:              str
    is_reachable:          bool
    latency_ms:            int
    available_models:      List[str]
    default_model_valid:   bool           # is configured default in available_models?
    credit_remaining:      Optional[int]  # from response headers; None if not exposed
    probed_at:             datetime       = field(default_factory=lambda: datetime.now(timezone.utc))
    error:                 Optional[str]  = None

    def __str__(self) -> str:
        status = "✅" if self.is_reachable else "❌"
        credit = f"  credit={self.credit_remaining}" if self.credit_remaining is not None else ""
        valid  = "✓" if self.default_model_valid else "✗ DEFAULT MODEL NOT FOUND"
        return (
            f"{status} {self.provider} | {self.latency_ms}ms | "
            f"{len(self.available_models)} models | default {valid}{credit}"
        )


# MARK: - Probe mixin (added to BaseProvider)

class ProbeMixin:
    """
    Mixin that adds probe() to any provider.
    Concrete providers must implement _models_url() and _model_name_key().
    """

    # Override in each provider
    def _models_url(self) -> str:
        raise NotImplementedError

    def _models_request_kwargs(self) -> dict:
        """Returns aiohttp request kwargs (headers, params, etc.)"""
        return {}

    def _model_name_key(self) -> str:
        """JSON key path to extract model name from list response."""
        return "name"   # override per provider if needed

    async def probe(self, timeout_s: float = 10.0) -> "ProviderStatus":
        """
        Probe this provider for reachability, latency, and model list.
        """
        url     = self._models_url()
        kwargs  = self._models_request_kwargs()
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        start   = time.time()

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, **kwargs) as response:
                    latency_ms = int((time.time() - start) * 1000)

                    if response.status != 200:
                        return ProviderStatus(
                            provider            = self.provider_name,
                            is_reachable        = False,
                            latency_ms          = latency_ms,
                            available_models    = [],
                            default_model_valid = False,
                            credit_remaining    = None,
                            error               = f"HTTP {response.status}"
                        )

                    data             = await response.json()
                    available        = self._extract_model_names(data)
                    default          = self.default_model
                    default_valid    = any(
                        default in m or m in default
                        for m in available
                    ) if available else False
                    credit           = self._extract_credit(response.headers)

                    return ProviderStatus(
                        provider            = self.provider_name,
                        is_reachable        = True,
                        latency_ms          = latency_ms,
                        available_models    = available,
                        default_model_valid = default_valid,
                        credit_remaining    = credit,
                    )

        except asyncio.TimeoutError:
            return ProviderStatus(
                provider            = self.provider_name,
                is_reachable        = False,
                latency_ms          = int((time.time() - start) * 1000),
                available_models    = [],
                default_model_valid = False,
                credit_remaining    = None,
                error               = "Timeout"
            )
        except Exception as e:
            return ProviderStatus(
                provider            = self.provider_name,
                is_reachable        = False,
                latency_ms          = int((time.time() - start) * 1000),
                available_models    = [],
                default_model_valid = False,
                credit_remaining    = None,
                error               = str(e)
            )

    def _extract_model_names(self, data: dict) -> List[str]:
        """
        Extract model name strings from the provider's list response.
        Override per provider if the response shape differs.
        """
        models = data.get("models", data.get("data", []))
        key    = self._model_name_key()
        names  = []
        for m in models:
            name = m.get(key, "")
            # Gemini prefixes with "models/" — strip it
            if "/" in name:
                name = name.split("/")[-1]
            if name:
                names.append(name)
        return sorted(names)

    def _extract_credit(self, headers) -> Optional[int]:
        """
        Extract remaining API credit/quota from response headers.
        Each provider uses a different header name.
        """
        # Try common header names in priority order
        for key in [
            "x-ratelimit-remaining-requests",   # OpenAI
            "anthropic-ratelimit-requests-remaining",  # Claude
            "x-goog-quota-remaining",            # Gemini
        ]:
            val = headers.get(key)
            if val is not None:
                try:
                    return int(val)
                except ValueError:
                    pass
        return None


# MARK: - Provider-specific probe config
# These are added to each provider class as small mixins

class GeminiProbeMixin(ProbeMixin):
    def _models_url(self) -> str:
        import os
        key = os.environ.get("GEMINI_API_KEY", "")
        return f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"

    def _model_name_key(self) -> str:
        return "name"   # Gemini returns {"name": "models/gemini-2.5-flash", ...}


class ClaudeProbeMixin(ProbeMixin):
    def _models_url(self) -> str:
        return "https://api.anthropic.com/v1/models"

    def _models_request_kwargs(self) -> dict:
        import os
        return {"headers": {
            "x-api-key":         os.environ.get("CLAUDE_API_KEY", ""),
            "anthropic-version": "2023-06-01"
        }}

    def _model_name_key(self) -> str:
        return "id"   # Claude returns {"id": "claude-sonnet-4-6", ...}


class OpenAIProbeMixin(ProbeMixin):
    def _models_url(self) -> str:
        return "https://api.openai.com/v1/models"

    def _models_request_kwargs(self) -> dict:
        import os
        return {"headers": {
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"
        }}

    def _model_name_key(self) -> str:
        return "id"   # OpenAI returns {"id": "gpt-4o", ...}


# MARK: - ProbeScheduler

class ProbeScheduler:
    """
    Runs probe() for all registered providers on startup and periodically.

    Usage:
        scheduler = ProbeScheduler(registry, interval_s=300)
        await scheduler.start()          # at Flask app startup
        status = scheduler.status("gemini")
    """

    def __init__(self, registry: Dict, interval_s: float = 300.0):
        self._registry  = registry    # same dict as LLMRouter.registry
        self._interval  = interval_s
        self._cache:    Dict[str, ProviderStatus] = {}
        self._task:     Optional[asyncio.Task]    = None

    async def start(self) -> None:
        """Run initial probe for all providers, then schedule periodic refresh."""
        await self._probe_all()
        self._task = asyncio.create_task(self._periodic_loop())
        print(f"🔍 ProbeScheduler started (interval={self._interval}s)")

    def stop(self) -> None:
        """Cancel the periodic refresh task."""
        if self._task:
            self._task.cancel()

    def status(self, provider: str) -> Optional[ProviderStatus]:
        """Last known status for a provider. None if not yet probed."""
        return self._cache.get(provider.lower())

    def all_status(self) -> Dict[str, ProviderStatus]:
        """Full status snapshot for all providers."""
        return dict(self._cache)

    def print_summary(self) -> None:
        """Print a human-readable status summary to stdout."""
        print("\n── Provider Status ─────────────────────────────────")
        for status in self._cache.values():
            print(f"  {status}")
        print()

    # MARK: - Private

    async def _probe_all(self) -> None:
        tasks = []
        for name, provider in self._registry.items():
            if isinstance(provider, ProbeMixin):
                tasks.append(self._probe_one(name, provider))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_one(self, name: str, provider: ProbeMixin) -> None:
        status = await provider.probe()
        self._cache[name.lower()] = status
        print(f"  {status}")

    async def _periodic_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            print("🔄 ProbeScheduler: refreshing provider status...")
            await self._probe_all()


# Module-level singleton — wired to LLMRouter.registry at startup
# Initialised lazily so import doesn't trigger network calls
scheduler: Optional[ProbeScheduler] = None


def init_scheduler(registry: Dict, interval_s: float = 300.0) -> ProbeScheduler:
    """
    Initialise the module-level scheduler with the router's registry.
    Call once at Flask app startup before awaiting scheduler.start().

        from core.llm.probe import init_scheduler, scheduler
        init_scheduler(router.registry)
        await scheduler.start()
    """
    global scheduler
    scheduler = ProbeScheduler(registry, interval_s)
    return scheduler
