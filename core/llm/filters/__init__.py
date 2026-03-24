# core/llm/filters/__init__.py
"""
Request and Response normalization layer.

Handles the 'plumbing' of converting raw prompts into provider-specific
payloads and extracting clean JSON from messy/hallucinated LLM responses.
"""

import re
import json
from typing import Any, Dict
from .xml_filter import dict_to_xml, wrap_in_tag
from ..errors import LLMError


class RequestFilter:
    """Base interface for constructing provider payloads."""

    def build_text_payload(self, prompt: str, model: str) -> Dict[str, Any]:
        raise NotImplementedError

    def build_image_payload(self, prompt: str, base64_image: str, model: str) -> Dict[str, Any]:
        raise NotImplementedError


class ResponseFilter:
    """Base interface for normalizing provider responses."""

    def extract_json(self, response: Dict[str, Any], provider: str) -> str:
        raise NotImplementedError

    def _strip_markdown(self, text: str) -> str:
        """Robustly removes markdown code fences and prefix/suffix chatter."""
        # 1. Look for ```json ... ``` or ``` ... ```
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 2. Fallback: find the first { and last }
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        return text.strip()


# MARK: - Gemini

class GeminiRequestFilter(RequestFilter):
    def build_text_payload(self, prompt: str, model: str) -> Dict[str, Any]:
        return {"contents": [{"parts": [{"text": prompt}]}]}

    def build_image_payload(self, prompt: str, base64_image: str, model: str) -> Dict[str, Any]:
        return {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                ]
            }]
        }


class GeminiResponseFilter(ResponseFilter):
    def extract_json(self, response: Dict[str, Any], provider: str) -> str:
        try:
            raw_text = response["candidates"][0]["content"]["parts"][0]["text"]
            return self._strip_markdown(raw_text)
        except (KeyError, IndexError):
            raise LLMError.decode_failed(provider, str(response))


# MARK: - Claude

class ClaudeRequestFilter(RequestFilter):
    def build_text_payload(self, prompt: str, model: str) -> Dict[str, Any]:
        framed_prompt = wrap_in_tag("instructions", prompt)
        return {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": framed_prompt}]
        }

    def build_image_payload(self, prompt: str, base64_image: str, model: str) -> Dict[str, Any]:
        return {
            "model": model,
            "max_tokens": 4096,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64_image
                        }
                    },
                    {"type": "text", "text": wrap_in_tag("instructions", prompt)}
                ]
            }]
        }


class ClaudeResponseFilter(ResponseFilter):
    def extract_json(self, response: Dict[str, Any], provider: str) -> str:
        try:
            raw_text = response["content"][0]["text"]
            return self._strip_markdown(raw_text)
        except (KeyError, IndexError):
            raise LLMError.decode_failed(provider, str(response))


# MARK: - OpenAI

class OpenAIRequestFilter(RequestFilter):
    def build_text_payload(self, prompt: str, model: str) -> Dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }

    def build_image_payload(self, prompt: str, base64_image: str, model: str) -> Dict[str, Any]:
        return {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }}
                ]
            }]
        }


class OpenAIResponseFilter(ResponseFilter):
    def extract_json(self, response: Dict[str, Any], provider: str) -> str:
        try:
            raw_text = response["choices"][0]["message"]["content"]
            return self._strip_markdown(raw_text)
        except (KeyError, IndexError):
            raise LLMError.decode_failed(provider, str(response))
