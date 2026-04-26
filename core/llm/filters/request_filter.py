#!/usr/bin/env python3

"""
=============================================================================
Module:        Request Filter
Location:      core/llm/filters/request_filter.py
Description:   Protocol and concrete implementations for outbound request
               transformation. Builds provider-specific HTTP payloads
               (OpenAI / Gemini / Claude) from a generic prompt + optional
               image input.

Architecture Note:
This layer is the symmetric mirror of ResponseFilter. It owns the
provider-specific quirks of payload shape (messages vs contents,
content-blocks vs text fields, image-URI vs inlineData encoding) so
callers upstream supply a uniform (prompt, model_tag) contract
regardless of which downstream LLM will receive the request.
Every payload is gated through SchemaValidator.validate_prompt_contract
to enforce the backend's flat-text transport policy.
=============================================================================
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict
from ..validator import SchemaValidator


class RequestFilter(ABC):
    """
    Interface for transforming a generic prompt into a provider-specific
    payload structure.
    """

    @abstractmethod
    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        """Build the provider-specific HTTP body for a text-only prompt."""

    @abstractmethod
    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        """Build the provider-specific HTTP body for a prompt + base64 image."""

    def encode_image(self, base64_string: str) -> str:
        """Hook for provider-specific image-encoding mutations. Default: pass-through."""
        return base64_string


class OpenAIRequestFilter(RequestFilter):
    """Builds payloads for OpenAI's `/v1/chat/completions` endpoint."""

    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        SchemaValidator.validate_prompt_contract(prompt)
        return {
            "model": model_tag,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "text"},
            "max_completion_tokens": 2048
        }

    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        SchemaValidator.validate_prompt_contract(prompt)
        return {
            "model": model_tag,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                ]
            }],
            "response_format": {"type": "text"},
            "max_completion_tokens": 2048
        }


class GeminiRequestFilter(RequestFilter):
    """Builds payloads for Google Gemini's `generateContent` endpoint."""

    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        SchemaValidator.validate_prompt_contract(prompt)
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "text/plain"
            }
        }

    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        SchemaValidator.validate_prompt_contract(prompt)
        return {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": "image/jpeg", "data": image_base64}}
                ]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "text/plain"
            }
        }


class ClaudeRequestFilter(RequestFilter):
    """Builds payloads for Anthropic Claude's `/v1/messages` endpoint."""

    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        SchemaValidator.validate_prompt_contract(prompt)
        return {
            "model": model_tag,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}]
        }

    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        SchemaValidator.validate_prompt_contract(prompt)
        return {
            "model": model_tag,
            "max_tokens": 2048,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}},
                    {"type": "text", "text": prompt}
                ]
            }]
        }
