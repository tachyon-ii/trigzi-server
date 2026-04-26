#!/usr/bin/env python3

"""
=============================================================================
Module:        Response Filter
Location:      core/llm/filters/response_filter.py
Description:   Protocol and concrete implementations for inbound response
               normalisation. Extracts raw text strings from provider-specific
               HTTP envelopes (Gemini / Claude / OpenAI).

Architecture Note:
This layer sits between the BaseProvider's HTTP transport and the
analyser's flat-text schema validation. It owns the provider-specific
quirks of error-shape, content-extraction, and markdown-fence stripping,
so callers downstream see a uniform string contract regardless of which
upstream LLM produced the response.
=============================================================================
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict
from ..errors import LLMError
from .xml_filter import XMLFilter

class ResponseFilter(ABC):  # pylint: disable=too-few-public-methods
    """Abstract base for provider-specific HTTP-response decoders.

    Subclasses normalise each LLM provider's response envelope into a
    plain string suitable for downstream flat-text schema validation.

    The single-public-method shape (``extract_json``) is intentional:
    these filters are dispatch units in a polymorphic strategy, not
    general-purpose objects.
    """

    @abstractmethod
    def extract_json(self, data: Dict[str, Any], provider_name: str) -> str:
        """
        Extract the raw string (formerly JSON) from the provider's HTTP response.
        Raises LLMError.decode_failed if extraction fails.
        """

# MARK: - Gemini Response Filter
class GeminiResponseFilter(ResponseFilter):  # pylint: disable=too-few-public-methods
    """Decodes responses from Google Gemini's `generateContent` endpoint."""

    def extract_json(self, data: Dict[str, Any], provider_name: str) -> str:
        if "error" in data:
            error_detail = data["error"]
            raise self._normalise_gemini_error(error_detail, provider_name)

        try:
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates found")

            text = candidates[0]["content"]["parts"][0]["text"]
            if not text:
                raise ValueError("Empty part text")

            return text.strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError.decode_failed(provider_name, raw=json.dumps(data)) from exc

    def _normalise_gemini_error(self, detail: Dict[str, Any], provider: str) -> LLMError:
        code = detail.get("code", 500)
        message = detail.get("message", "Unknown error")
        if code == 429:
            return LLMError.rate_limited(provider, retry_after=None)
        return LLMError.server_error(provider, status_code=code, message=message)


# MARK: - Claude Response Filter
class ClaudeResponseFilter(ResponseFilter):  # pylint: disable=too-few-public-methods
    """Decodes responses from Anthropic Claude's `/v1/messages` endpoint."""

    def extract_json(self, data: Dict[str, Any], provider_name: str) -> str:
        if data.get("type") == "error":
            error_detail = data.get("error", {})
            raise self._normalise_claude_error(error_detail, provider_name)

        content_blocks = data.get("content", [])
        text_parts = [block["text"] for block in content_blocks if block.get("type") == "text" and block.get("text")]
        raw_text = "".join(text_parts)

        if not raw_text:
            raise LLMError.empty_response(provider_name)

        # Strip <thinking>…</thinking> blocks before decoding
        cleaned = XMLFilter.strip_thinking(raw_text).strip()

        # The JSON `{` check is REMOVED to support flat-text extraction

        return cleaned

    def _normalise_claude_error(self, detail: Dict[str, Any], provider: str) -> LLMError:
        error_type = detail.get("type", "")
        message = detail.get("message", "Unknown error")
        if error_type == "rate_limit_error":
            return LLMError.rate_limited(provider, retry_after=None)
        if error_type in ["overloaded_error", "api_error"]:
            return LLMError.server_error(provider, status_code=503, message=message)
        if error_type == "invalid_request_error":
            return LLMError.invalid_request(reason=message)
        return LLMError.server_error(provider, status_code=500, message=message)


# MARK: - OpenAI Response Filter
class OpenAIResponseFilter(ResponseFilter):  # pylint: disable=too-few-public-methods
    """Decodes responses from OpenAI's `/v1/chat/completions` endpoint."""

    def extract_json(self, data: Dict[str, Any], provider_name: str) -> str:
        if "error" in data:
            error_detail = data["error"]
            raise self._normalise_openai_error(error_detail, provider_name)

        try:
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("No choices returned")

            text = choices[0]["message"]["content"]
            if not text:
                raise ValueError("Empty choice content")

            # Defensive markdown fence stripping (in case the model is stubborn)
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```text"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

            cleaned = text.strip()

            # The JSON `{` check is REMOVED to support flat-text extraction

            return cleaned

        except (KeyError, IndexError, ValueError) as exc:
            # Truncation [200] removed to ensure full visibility on crashes
            raise LLMError.decode_failed(provider_name, raw=json.dumps(data)) from exc

    def _normalise_openai_error(self, detail: Dict[str, Any], provider: str) -> LLMError:
        error_type = detail.get("type", "")
        message = detail.get("message", "Unknown error")
        if error_type in ["insufficient_quota", "rate_limit_exceeded"]:
            return LLMError.rate_limited(provider, retry_after=None)
        if error_type == "server_error":
            return LLMError.server_error(provider, status_code=500, message=message)
        if error_type == "invalid_request_error":
            return LLMError.invalid_request(reason=message)
        return LLMError.server_error(provider, status_code=500, message=message)
