"""
=============================================================================
Module:        OpenAI Provider
Location:      core/llm/providers/openai.py
Description:   Concrete BaseProvider implementation for OpenAI.
               Wires the request/response filters, probe mixin, and the
               /v1/chat/completions endpoint together with Bearer auth.

Architecture Note:
This class is a thin glue layer over BaseProvider — all transport,
retry, and failover logic lives upstream. Its only responsibility
is to bind OpenAI-specific URL, headers, and filter instances.
=============================================================================
"""

import os
from .base import BaseProvider
from ..filters import OpenAIRequestFilter, OpenAIResponseFilter
from ..config import config
from ..probe import OpenAIProbeMixin


class OpenAIProvider(OpenAIProbeMixin, BaseProvider):
    """OpenAI provider client. See :class:`BaseProvider` for the contract."""

    @property
    def provider_name(self) -> str:
        return "OpenAI"

    @property
    def default_model(self) -> str:
        return config.primary_model("openai")

    @property
    def request_filter(self):
        return OpenAIRequestFilter()

    @property
    def response_filter(self):
        return OpenAIResponseFilter()

    def build_url(self, model_tag: str) -> str:
        return "https://api.openai.com/v1/chat/completions"

    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"
        }
