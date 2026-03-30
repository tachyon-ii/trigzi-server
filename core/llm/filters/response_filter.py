#!/usr/bin/env python3
from __future__ import annotations
#
#  core/llm/filters/response_filter.py
#  trigzi-backend
#
#  Protocol + concrete implementations for inbound response normalisation.
#  Extracts raw text strings from provider-specific HTTP envelopes.
#

import json
from abc import ABC, abstractmethod
from typing import Any, Dict
from ..errors import LLMError
from .xml_filter import XMLFilter

class ResponseFilter(ABC):
    @abstractmethod
    def extract_json(self, data: Dict[str, Any], provider_name: str) -> str:
        """
        Extract the raw string (formerly JSON) from the provider's HTTP response.
        Raises LLMError.decode_failed if extraction fails.
        """
        pass

# MARK: - Gemini Response Filter
class GeminiResponseFilter(ResponseFilter):
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
        except (KeyError, IndexError, ValueError):
            raise LLMError.decode_failed(provider_name, raw=json.dumps(data))

    def _normalise_gemini_error(self, detail: Dict[str, Any], provider: str) -> LLMError:
        code = detail.get("code", 500)
        message = detail.get("message", "Unknown error")
        if code == 429:
            return LLMError.rate_limited(provider, retry_after=None)
        return LLMError.server_error(provider, status_code=code, message=message)


# MARK: - Claude Response Filter
class ClaudeResponseFilter(ResponseFilter):
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
        elif error_type in ["overloaded_error", "api_error"]:
            return LLMError.server_error(provider, status_code=503, message=message)
        elif error_type == "invalid_request_error":
            return LLMError.invalid_request(reason=message)
        return LLMError.server_error(provider, status_code=500, message=message)


# MARK: - OpenAI Response Filter
class OpenAIResponseFilter(ResponseFilter):
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

        except (KeyError, IndexError, ValueError):
            # Truncation [200] removed to ensure full visibility on crashes
            raise LLMError.decode_failed(provider_name, raw=json.dumps(data))

    def _normalise_openai_error(self, detail: Dict[str, Any], provider: str) -> LLMError:
        error_type = detail.get("type", "")
        message = detail.get("message", "Unknown error")
        if error_type in ["insufficient_quota", "rate_limit_exceeded"]:
            return LLMError.rate_limited(provider, retry_after=None)
        elif error_type == "server_error":
            return LLMError.server_error(provider, status_code=500, message=message)
        elif error_type == "invalid_request_error":
            return LLMError.invalid_request(reason=message)
        return LLMError.server_error(provider, status_code=500, message=message)
