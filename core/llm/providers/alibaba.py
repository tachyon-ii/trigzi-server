"""
=============================================================================
Module:        Alibaba Provider (Qwen)
Location:      core/llm/providers/alibaba.py
Description:   Concrete BaseProvider implementation for Alibaba Cloud's
               Qwen models via the OpenAI-compatible DashScope endpoint.
               Wires the standard OpenAI request/response filters (the API
               is compatible-mode) with Alibaba auth and base URL.

Architecture Note:
Thin glue layer — identical shape to OpenAIProvider but points at the
Alibaba endpoint and reads ALIBABA_API_KEY. No custom filters needed
because the compatible-mode endpoint speaks the OpenAI wire format.
=============================================================================
"""

import os
from .base import BaseProvider
from ..filters import OpenAIRequestFilter, OpenAIResponseFilter
from ..config import config
from ..probe import OpenAIProbeMixin

_BASE_URL = (
    os.environ.get("ALIBABA_BASE_URL", "").rstrip("/")
    or "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)


class AlibabaProbeMixin(OpenAIProbeMixin):
    """Probe mixin for Alibaba's OpenAI-compatible DashScope endpoint."""

    def _models_url(self) -> str:
        return f"{_BASE_URL}/models"

    def _models_request_kwargs(self) -> dict:
        return {"headers": {
            "Authorization": f"Bearer {os.environ.get('ALIBABA_API_KEY', '')}"
        }}

    # Terms that identify non-text models: multimodal, audio, image-gen, etc.
    _NOISE = [
        "image", "tts", "asr", "vl", "omni", "realtime", "mt",
        "livetranslate", "s2s", "embedding", "coder", "wan",
        "z-image", "ccai", "tongyi", "qvq", "slp", "character",
    ]

    def _extract_model_names(self, data: dict) -> list:
        """Return only text-generation models, newest first."""
        models = data.get("data", [])
        models.sort(key=lambda x: x.get("created", 0), reverse=True)
        names = []
        for m in models:
            name = m.get("id", "")
            if not name:
                continue
            low = name.lower()
            if any(noise in low for noise in self._NOISE):
                continue
            names.append(name)
        return names


class AlibabaProvider(AlibabaProbeMixin, BaseProvider):
    """Alibaba Cloud Qwen provider. See :class:`BaseProvider` for the contract."""

    @property
    def provider_name(self) -> str:
        return "Alibaba"

    @property
    def default_model(self) -> str:
        return config.primary_model("alibaba")

    @property
    def request_filter(self):
        return OpenAIRequestFilter()

    @property
    def response_filter(self):
        return OpenAIResponseFilter()

    def build_url(self, model_tag: str) -> str:
        return f"{_BASE_URL}/chat/completions"

    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {os.environ.get('ALIBABA_API_KEY', '')}"
        }
