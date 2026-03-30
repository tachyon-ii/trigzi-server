#!/usr/bin/env python3
from __future__ import annotations
#
#  core/llm/filters/request_filter.py
#  trigzi-backend
#
#  Protocol + concrete implementations for outbound request transformation.
#  Enforces flat-text transport modes and injects validated prompts.
#

from abc import ABC, abstractmethod
from typing import Any, Dict
from ..validator import SchemaValidator


class RequestFilter(ABC):
    """
    Interface for transforming a generic prompt into a provider-specific 
    payload structure.
    """
    @abstractmethod
    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]: pass

    @abstractmethod
    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]: pass

    def encode_image(self, base64_string: str) -> str:
        return base64_string


class OpenAIRequestFilter(RequestFilter):
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
