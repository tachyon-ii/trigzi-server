# core/llm/probe.py
"""
Provider health-check and introspection layer.

Implements probe() for each provider — a lightweight operation that:
  1. Hits the provider's model-list endpoint to confirm reachability
  2. Measures round-trip latency
  3. Returns the list of available models (clean identifiers only)
  4. Returns per-model metadata where the provider exposes it
  5. Validates that the configured default model is in that list
  6. Reports remaining API credit where available via response headers

Credit remaining is also updated on every normal analyse() call via
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

Data/presentation separation (April 2026):
    available_models is a list of clean model identifiers — no padding,
    no date suffixes, no terminal formatting. Per-model metadata
    (e.g. OpenAI's `created` timestamp) lives in model_metadata, keyed
    by model name. Display formatting is the responsibility of the
    caller (e.g. scripts/probe_live.py).
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
    available_models:      List[str]               # clean identifiers, e.g. "gpt-5.5-pro"
    default_model_valid:   bool                    # is configured default in available_models?
    credit_remaining:      Optional[int]           # from response headers; None if not exposed
    model_metadata:        Dict[str, dict]         = field(default_factory=dict)
    """
    Optional per-model metadata, keyed by model name. Providers that expose
    structured info (e.g. OpenAI's `created` timestamp) populate this; others
    leave it empty. Schema is provider-specific. Known fields:
      - openai: {"created_at": "YYYY-MM-DD"}
    """
    probed_at:             datetime                = field(default_factory=lambda: datetime.now(timezone.utc))
    error:                 Optional[str]           = None

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
                    metadata         = self._extract_model_metadata(data)
                    default          = self.default_model
                    # Exact match — names are now clean (no padding/suffixes)
                    default_valid    = default in available if available else False
                    credit           = self._extract_credit(response.headers)

                    return ProviderStatus(
                        provider            = self.provider_name,
                        is_reachable        = True,
                        latency_ms          = latency_ms,
                        available_models    = available,
                        default_model_valid = default_valid,
                        credit_remaining    = credit,
                        model_metadata      = metadata,
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

    def _extract_model_metadata(self, data: dict) -> Dict[str, dict]:
        """
        Extract per-model metadata keyed by model name. Default is empty —
        providers with structured per-model info (e.g. OpenAI's release
        timestamps) override this. Schema is provider-specific.
        """
        return {}

    def _extract_credit(self, headers) -> Optional[int]:
        """
        Extract remaining API credit/quota from response headers.
        Each provider uses a different header name.

        Note: Gemini is intentionally absent from this list. Google's
        generativelanguage.googleapis.com does not expose remaining-quota
        in successful response headers at all — quota information is
        only returned inside 429 error bodies as structured QuotaFailure
        details. So no header lookup can return a number for Gemini.
        """
        # Try common header names in priority order
        for key in [
            "x-ratelimit-remaining-requests",        # OpenAI
            "x-ratelimit-remaining",                 # generic
            "anthropic-ratelimit-requests-remaining", # Anthropic
        ]:
            if key in headers:
                try:
                    return int(headers[key])
                except (ValueError, TypeError):
                    pass
        return None


# MARK: - Per-provider probe mixins

class GeminiProbeMixin(ProbeMixin):
    def _models_url(self) -> str:
        import os
        key = os.environ.get("GEMINI_API_KEY", "")
        return f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"

    def _model_name_key(self) -> str:
        return "name"   # Gemini returns {"name": "models/gemini-2.5-flash", ...}

    def _extract_model_names(self, data: dict) -> List[str]:
        """
        Aggressively filter the Gemini model list to return 
        only stable, text-capable reasoning engines.
        """
        valid_models = []
        
        # Exclusion list to strip multimodal, experimental, and embedding noise
        noise_filters = [
            "preview", "audio", "vision", "embedding", "imagen", 
            "veo", "lyria", "nano-banana", "aqa", "robotics", 
            "tts", "computer-use", "001" # Drops legacy static versions in favor of 'latest' aliases
        ]

        for model in data.get("models", []):
            # 1. API Capability check: Must be a text generator
            if "generateContent" not in model.get("supportedGenerationMethods", []):
                continue
            
            # Strip the arbitrary Google prefix
            name = model.get("name", "").replace("models/", "")
            
            # 2. Semantic name check: Drop the laboratory noise
            if any(noise in name.lower() for noise in noise_filters):
                continue
                
            valid_models.append(name)

        return sorted(valid_models)


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

    def _extract_model_names(self, data: dict) -> List[str]:
        """
        Return clean OpenAI model identifiers, sorted by `created` timestamp
        descending (newest first). The `created` timestamp is preserved
        separately via _extract_model_metadata().
        """
        models = data.get("data", [])
        models.sort(key=lambda x: x.get("created", 0), reverse=True)

        names = []
        for m in models:
            name = m.get("id", "")
            if name:
                names.append(name)
        return names

    def _extract_model_metadata(self, data: dict) -> Dict[str, dict]:
        """
        Expose OpenAI's per-model `created` unix timestamp as YYYY-MM-DD,
        keyed by model name. Used by scripts/probe_live.py to render the
        terminal display, and by scripts/probe_models.py if recency-aware
        ranking is ever wanted.
        """
        out: Dict[str, dict] = {}
        for m in data.get("data", []):
            name = m.get("id", "")
            ts   = m.get("created", 0)
            if not name:
                continue
            if ts:
                date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                out[name] = {"created_at": date_str}
            else:
                out[name] = {"created_at": None}
        return out


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
