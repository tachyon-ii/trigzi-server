# core/llm/config.py
"""
LLM provider configuration manager.

Loads llm_providers.json once at server startup and vends model
configuration to the router and providers. Replaces all hardcoded model
strings — adding or updating a model requires only a JSON edit.

Thread-safe singleton: the config object is initialised exactly once
using a threading.Lock, guaranteeing zero race conditions during
concurrent request handling and zero disk I/O latency after startup.

The module-level `config` instance is the intended import target:

    from core.llm.config import config

    primary = config.primary_model("gemini")      # "gemini-2.5-flash"
    chain   = config.hierarchy("gemini")          # ["gemini-2.5-flash", ...]
    cost    = config.estimate_cost(              
                  "gemini-2.5-flash",
                  input_tokens=800,
                  output_tokens=200
              )

JSON location: core/llm/llm_providers.json
(same directory as this file — consistent with Swift's Resources/ pattern)
"""

import json
from typing import Optional, List
import os
import threading


class LLMProviderConfig:
    """
    Thread-safe singleton config manager.

    Do not instantiate directly — use the module-level `config` instance.
    """

    _instance = None
    _lock     = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._load_config()
        return cls._instance

    # ------------------------------------------------------------------ #
    # Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def _load_config(self) -> None:
        """
        Load llm_providers.json from the same directory as this file.

        Falls back to a minimal inline config if the file is missing so
        the server degrades gracefully rather than crashing on startup.
        The fallback models match the current llm_providers.json defaults
        so behaviour is consistent.
        """
        config_path = os.path.join(os.path.dirname(__file__), "llm_providers.json")

        # Minimal fallback — mirrors hierarchy[0] from llm_providers.json
        _fallback = {
            "providers": {
                "gemini": {
                    "defaultModel": "gemini-2.5-flash",
                    "models": {},
                    "hierarchy": ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
                },
                "claude": {
                    "defaultModel": "claude-sonnet-4-6",
                    "models": {},
                    "hierarchy": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
                },
                "openai": {
                    "defaultModel": "gpt-4o",
                    "models": {},
                    "hierarchy": ["gpt-4o", "gpt-4.1-mini"]
                }
            }
        }

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            provider_count = len(self._config.get("providers", {}))
            print(f"✅ LLMProviderConfig: loaded {provider_count} providers "
                  f"from {config_path}")
        else:
            print(f"⚠️  LLMProviderConfig: {config_path} not found — "
                  f"using built-in fallback config.")
            self._config = _fallback

    # ------------------------------------------------------------------ #
    # Model resolution                                                     #
    # ------------------------------------------------------------------ #

    def resolve(self, model_tag: Optional[str], provider: str) -> str:
        """
        Return the full model string to use on the wire.

        Resolution order:
          1. None / empty → provider's defaultModel
          2. Tag equals provider name → provider's defaultModel
          3. Tag is a known model in the catalogue → return as-is
          4. Unknown tag → pass through (lets the API reject if invalid;
             allows new models to work before llm_providers.json is updated)

        Args:
            model_tag: Model string from the request, or None.
            provider:  Provider name (case-insensitive: "gemini", "claude", etc.)

        Returns:
            Full model string ready to embed in the API payload.
        """
        entry = self._config.get("providers", {}).get(provider.lower(), {})
        default = entry.get("defaultModel", provider)

        if not model_tag:
            return default

        if model_tag.lower() == provider.lower():
            return default

        # Known in catalogue — return verbatim
        if model_tag in entry.get("models", {}):
            return model_tag

        # Unknown — pass through and let the API surface any error
        return model_tag

    # ------------------------------------------------------------------ #
    # Hierarchy                                                            #
    # ------------------------------------------------------------------ #

    def hierarchy(self, provider: str) -> List[str]:
        """
        Return the ordered model list for automatic tier selection.

        hierarchy[0] = primary   (highest capability / default)
        hierarchy[1] = secondary (first fallback)
        hierarchy[2] = tertiary  (last resort)

        Args:
            provider: Provider name (case-insensitive).

        Returns:
            List of model strings in priority order. Empty list if the
            provider is not in the config.
        """
        return (self._config
                .get("providers", {})
                .get(provider.lower(), {})
                .get("hierarchy", []))

    def primary_model(self, provider: str) -> str:
        """Primary model (hierarchy index 0) for a provider."""
        h = self.hierarchy(provider)
        if h:
            return h[0]
        return (self._config
                .get("providers", {})
                .get(provider.lower(), {})
                .get("defaultModel", provider))

    def secondary_model(self, provider: str) -> Optional[str]:
        """Secondary model (hierarchy index 1), or None if not configured."""
        h = self.hierarchy(provider)
        return h[1] if len(h) > 1 else None

    def tertiary_model(self, provider: str) -> Optional[str]:
        """Tertiary model (hierarchy index 2), or None if not configured."""
        h = self.hierarchy(provider)
        return h[2] if len(h) > 2 else None

    # ------------------------------------------------------------------ #
    # Cost estimation                                                      #
    # ------------------------------------------------------------------ #

    def estimate_cost(self, model: str, input_tokens: int,
                      output_tokens: int) -> Optional[float]:
        """
        Estimate call cost in USD using per-million-token rates from config.

        Args:
            model:         Full model string (e.g. "gemini-2.5-flash").
            input_tokens:  Number of input/prompt tokens consumed.
            output_tokens: Number of output/completion tokens produced.

        Returns:
            Estimated cost in USD, or None if the model is not in the
            catalogue (e.g. a pass-through unknown model).
        """
        for entry in self._config.get("providers", {}).values():
            model_cfg = entry.get("models", {}).get(model)
            if model_cfg:
                input_cost  = (input_tokens  / 1_000_000) * model_cfg.get("input_per_million",  0)
                output_cost = (output_tokens / 1_000_000) * model_cfg.get("output_per_million", 0)
                return round(input_cost + output_cost, 8)
        return None

    def supports_vision(self, model: str) -> bool:
        """
        Return True if the model supports image/vision input.

        Defaults to False for unknown models — conservative is safer
        than optimistic when deciding whether to encode and send an image.

        Args:
            model: Full model string.
        """
        for entry in self._config.get("providers", {}).values():
            model_cfg = entry.get("models", {}).get(model)
            if model_cfg is not None:
                return bool(model_cfg.get("vision", False))
        return False

    def cheapest_model(self, provider: str) -> Optional[str]:
        """
        Return the cheapest model in a provider's hierarchy by output cost.

        Output token cost is used as the primary signal because it dominates
        total cost for generation-heavy tasks like food analysis.

        Args:
            provider: Provider name (case-insensitive).

        Returns:
            Model string of the cheapest model, or None if the provider
            has no costed models in the catalogue.
        """
        entry = self._config.get("providers", {}).get(provider.lower(), {})
        models = entry.get("models", {})
        hierarchy = entry.get("hierarchy", [])

        candidates = [
            (tag, models[tag].get("output_per_million", float("inf")))
            for tag in hierarchy
            if tag in models
        ]

        if not candidates:
            return None

        return min(candidates, key=lambda x: x[1])[0]

    # ------------------------------------------------------------------ #
    # Introspection                                                        #
    # ------------------------------------------------------------------ #

    @property
    def all_provider_names(self) -> List[str]:
        """Sorted list of all configured provider names."""
        return sorted(self._config.get("providers", {}).keys())


# Module-level singleton — import this, not the class.
config = LLMProviderConfig()
