#!/usr/bin/env python3
"""
=============================================================================
Module:        Base LLM Provider
Location:      core/llm/providers/base.py
Description:   Abstract base class and execution harness for LLM providers.
               Enforces a unified interface for API request formatting, HTTP
               transport, and response normalization across OpenAI, Claude,
               and Gemini.

Architecture Note: This class intercepts raw HTTP responses and delegates strict
parsing to the SchemaValidator. If a provider hallucinates or
fails to return the expected schema, this layer raises an
LLMError.decode_failed, which triggers the Router to safely
failover to the next model in the hierarchy.
=============================================================================
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Any, List, Optional
import aiohttp
from ..errors import LLMError
from ..skills import SkillsLibrary
from ..filters import RequestFilter, ResponseFilter
from ..validator import SchemaValidator

class BaseProvider:
    """Abstract base class for LLM provider clients.

    Defines the unified contract every concrete provider (OpenAI, Claude,
    Gemini) must implement: a ``request_filter`` for outbound payload
    shaping, a ``response_filter`` for inbound response decoding, plus the
    URL/header/auth details specific to each provider.

    The ``analyse`` method is the single public entry point. Subclasses
    should not override it; they only override the abstract properties
    (``default_model``, ``request_filter``, ``response_filter``,
    ``build_url``, ``headers``).
    """

    @property
    def provider_name(self) -> str:
        """Human-readable provider identifier used in logs and errors."""
        return "BaseProvider"

    @property
    def default_model(self) -> str:
        """Model tag used when the caller passes ``None`` or empty string."""
        raise NotImplementedError

    @property
    def request_filter(self) -> RequestFilter:
        """The provider-specific outbound payload builder."""
        raise NotImplementedError

    @property
    def response_filter(self) -> ResponseFilter:
        """The provider-specific inbound response decoder."""
        raise NotImplementedError

    def build_url(self, model_tag: str) -> str:
        """Return the full POST URL for a given model tag."""
        raise NotImplementedError

    def headers(self) -> Dict[str, str]:
        """Return provider-specific HTTP headers (auth, content-type, etc.)."""
        return {}

    @property
    def max_retries(self) -> int:
        """Number of retries on failover-eligible errors before giving up."""
        return 2

    @property
    def retry_delay(self) -> float:
        """Wait time in seconds between retry attempts."""
        return 1.0

    async def analyse(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        payload_data: Dict[str, Any],
        profile: str,
        model_tag: str,
        timeout_s: float = 30.0,
        expected_keys: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Execute one analysis call against this provider with retry-on-failover.

        Calls :meth:`_perform_request` to do the HTTP round-trip, then runs
        :meth:`SchemaValidator.extract_blocks` against ``expected_keys`` if
        supplied. A schema-extraction failure raises
        :class:`LLMError.decode_failed`, which the router treats as
        failover-eligible — i.e. the next model in the hierarchy will be
        tried. Network-timeout, rate-limit, and server-error responses are
        also failover-eligible. Non-failoverable errors (like
        :class:`LLMError.invalid_request`) propagate immediately.
        """

        tag      = model_tag if model_tag else self.default_model
        last_err = LLMError.unknown_provider(self.provider_name)

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self.retry_delay)
                print(f"🎶️ [{self.provider_name}] Retry {attempt}/{self.max_retries}")

            try:
                start_time  = time.time()
                raw_string  = await self._perform_request(payload_data, profile, tag, timeout_s)
                latency_ms  = int((time.time() - start_time) * 1000)

                # TRIGGER FAILOVER IF SCHEMA EXTRACTION FAILS
                parsed_blocks = []
                if expected_keys:
                    parsed_blocks = SchemaValidator.extract_blocks(raw_string, expected_keys)
                    if not parsed_blocks:
                        raise LLMError.decode_failed(self.provider_name, raw=raw_string)

                return {
                    "result":       raw_string,
                    "parsed_blocks": parsed_blocks,
                    "model":        tag,
                    "provider":     self.provider_name,
                    "latency_ms":   latency_ms,
                    "raw_json":     raw_string,
                    "was_fallback": False,
                }

            except LLMError as err:
                last_err = err
                if not err.is_failoverable:
                    raise err

                print(f"⚠️ [{self.provider_name}] Attempt {attempt + 1} failed: {err}")

                if err.raw_response:
                    print(f"🔻 --- RAW UNREADABLE RESPONSE [{self.provider_name}] --- 🔻")
                    print(err.raw_response)
                    print(f"🔺 {'-' * 40} 🔺")

        raise last_err

    async def _perform_request(
        self,
        payload_data: Dict[str, Any],
        profile: str,
        model_tag: str,
        timeout_s: float
    ) -> str:
        """Build the request payload, POST it, and return the decoded string body.

        Selects a payload-shaping path based on which key is present in
        ``payload_data`` (``image_base64`` / ``product`` / ``menu_text`` /
        ``text`` / ``prompt``). Translates HTTP-level failures into typed
        :class:`LLMError` instances so the caller can decide whether to
        retry, fail over, or propagate.
        """

        url = self.build_url(model_tag)

        # Payload dispatch
        if "image_base64" in payload_data:
            prompt  = SkillsLibrary.analyse_food_image_prompt(profile)
            payload = self.request_filter.build_image_payload(
                prompt, payload_data["image_base64"], model_tag
            )

        elif "product" in payload_data:
            prompt  = (payload_data.get("prompt")
                        or SkillsLibrary.enrich_product_prompt(payload_data["product"]))
            payload = self.request_filter.build_text_payload(prompt, model_tag)

        elif "menu_text" in payload_data:
            prompt  = SkillsLibrary.analyse_menu_prompt(payload_data["menu_text"])
            payload = self.request_filter.build_text_payload(prompt, model_tag)

        elif "text" in payload_data:
            prompt  = SkillsLibrary.analyse_text_prompt(payload_data["text"], profile)
            payload = self.request_filter.build_text_payload(prompt, model_tag)

        elif "prompt" in payload_data:
            payload = self.request_filter.build_text_payload(
                payload_data["prompt"], model_tag
            )

        else:
            raise LLMError.invalid_request(
                "Payload must contain 'image_base64', 'product', 'menu_text', 'text', or 'prompt'"
            )

        headers = self.headers()
        headers["Content-Type"] = "application/json"

        timeout = aiohttp.ClientTimeout(total=timeout_s)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    content_type = response.headers.get("Content-Type", "")

                    if "application/json" in content_type:
                        resp_json = await response.json()
                        return self.response_filter.extract_json(resp_json, self.provider_name)

                    resp_text = await response.text()
                    if response.status == 429:
                        raise LLMError.rate_limited(self.provider_name)
                    raise LLMError.server_error(
                        self.provider_name, response.status, resp_text[:200]
                    )

        except asyncio.TimeoutError as exc:
            raise LLMError.network_timeout(self.provider_name) from exc
        except aiohttp.ClientError as exc:
            raise LLMError.network_failure(self.provider_name, str(exc)) from exc
