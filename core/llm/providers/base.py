# core/llm/providers/base.py
import asyncio
import aiohttp
import json
import time
from typing import Dict, Any
from ..errors import LLMError
from ..skills import SkillsLibrary
from ..filters import RequestFilter, ResponseFilter


class BaseProvider:
    """
    Abstract base class for all LLM providers.
    Ported from BaseProvider.swift.
    Handles retries, timeouts, HTTP transport, and response normalisation.
    """

    @property
    def provider_name(self) -> str:
        return "BaseProvider"

    @property
    def default_model(self) -> str:
        raise NotImplementedError

    @property
    def request_filter(self) -> RequestFilter:
        raise NotImplementedError

    @property
    def response_filter(self) -> ResponseFilter:
        raise NotImplementedError

    def build_url(self, model_tag: str) -> str:
        raise NotImplementedError

    def headers(self) -> Dict[str, str]:
        return {}

    @property
    def max_retries(self) -> int:
        return 2

    @property
    def retry_delay(self) -> float:
        return 1.0

    async def analyze(
        self,
        payload_data: Dict[str, Any],
        profile: str,
        model_tag: str,
        timeout_s: float = 30.0
    ) -> Dict[str, Any]:

        tag = model_tag if model_tag else self.default_model
        last_err = LLMError.unknown_provider(self.provider_name)

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self.retry_delay)
                print(f"♻️ [{self.provider_name}] Retry {attempt}/{self.max_retries}")

            try:
                start_time = time.time()
                json_string = await self._perform_request(payload_data, profile, tag, timeout_s)
                latency_ms = int((time.time() - start_time) * 1000)

                try:
                    result_obj = json.loads(json_string)
                except json.JSONDecodeError:
                    raise LLMError.decode_failed(self.provider_name, json_string)

                return {
                    "result":      result_obj,
                    "model":       tag,
                    "provider":    self.provider_name,
                    "latency_ms":  latency_ms,
                    "raw_json":    json_string,
                    "was_fallback": False
                }

            except LLMError as err:
                last_err = err
                if not err.is_failoverable:
                    raise err
                print(f"⚠️ [{self.provider_name}] Attempt {attempt + 1} failed: {err}")

        raise last_err

    async def _perform_request(
        self,
        payload_data: Dict[str, Any],
        profile: str,
        model_tag: str,
        timeout_s: float
    ) -> str:

        url = self.build_url(model_tag)

        if "text" in payload_data:
            prompt  = SkillsLibrary.analyze_text_prompt(payload_data["text"], profile)
            payload = self.request_filter.build_text_payload(prompt, model_tag)
        elif "image_base64" in payload_data:
            prompt  = SkillsLibrary.analyze_food_image_prompt(profile)
            payload = self.request_filter.build_image_payload(prompt, payload_data["image_base64"], model_tag)
        else:
            raise LLMError.invalid_request("Payload must contain 'text' or 'image_base64'")

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
                    else:
                        resp_text = await response.text()
                        if response.status == 429:
                            raise LLMError.rate_limited(self.provider_name)
                        raise LLMError.server_error(self.provider_name, response.status, resp_text[:200])

        except asyncio.TimeoutError:
            raise LLMError.network_timeout(self.provider_name)
        except aiohttp.ClientError as e:
            raise LLMError.network_failure(self.provider_name, str(e))

