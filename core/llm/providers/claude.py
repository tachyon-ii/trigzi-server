"""
=============================================================================
Module:        Claude Provider
Location:      core/llm/providers/claude.py
Description:   Concrete BaseProvider implementation for Anthropic Claude.
               Wires the request/response filters, probe mixin, and the
               /v1/messages endpoint together with x-api-key auth.

Architecture Note:
This class is a thin glue layer over BaseProvider — all transport,
retry, and failover logic lives upstream. Its only responsibility
is to bind Anthropic-specific URL, headers, and filter instances.
=============================================================================
"""

import os
from .base import BaseProvider
from ..filters import ClaudeRequestFilter, ClaudeResponseFilter
from ..config import config
from ..probe import ClaudeProbeMixin


class ClaudeProvider(ClaudeProbeMixin, BaseProvider):
    """Anthropic Claude provider client. See :class:`BaseProvider` for the contract."""

    @property
    def provider_name(self) -> str:
        return "Claude"

    @property
    def default_model(self) -> str:
        return config.primary_model("claude")

    @property
    def request_filter(self):
        return ClaudeRequestFilter()

    @property
    def response_filter(self):
        return ClaudeResponseFilter()

    def build_url(self, model_tag: str) -> str:
        return "https://api.anthropic.com/v1/messages"

    def headers(self) -> dict:
        return {
            "x-api-key":         os.environ.get("CLAUDE_API_KEY", ""),
            "anthropic-version": "2023-06-01"
        }
