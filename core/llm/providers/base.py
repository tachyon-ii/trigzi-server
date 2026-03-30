# core/llm/providers/base.py

import asyncio
import aiohttp
import time
from typing import Dict, Any
from ..errors import LLMError
from ..skills import SkillsLibrary
from ..filters import RequestFilter, ResponseFilter


class BaseProvider:

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

        tag      = model_tag if model_tag else self.default_model
        last_err = LLMError.unknown_provider(self.provider_name)

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self.retry_delay)
                print(f"♻️ [{self.provider_name}] Retry {attempt}/{self.max_retries}")

            try:
                start_time  = time.time()
                raw_string  = await self._perform_request(payload_data, profile, tag, timeout_s)
                latency_ms  = int((time.time() - start_time) * 1000)

                # ARCHITECTURE UPDATE: The router is now a dumb pipe.
                # No JSON decoding happens here. It just returns the raw string.
                return {
                    "result":       raw_string,
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
                
                # --- INJECT RAW DUMP HERE ---
                if err.raw_response:
                    print(f"🔻 --- RAW UNREADABLE RESPONSE [{self.provider_name}] --- 🔻")
                    print(err.raw_response)
                    print(f"🔺 {'-' * 40} 🔺")
                # ----------------------------

        raise last_err

    async def _perform_request(
        self,
        payload_data: Dict[str, Any],
        profile: str,
        model_tag: str,
        timeout_s: float
    ) -> str:

        url = self.build_url(model_tag)

        # Payload dispatch
        if "image_base64" in payload_data:
            prompt  = SkillsLibrary.analyze_food_image_prompt(profile)
            payload = self.request_filter.build_image_payload(
                prompt, payload_data["image_base64"], model_tag
            )

        elif "product" in payload_data:
            prompt  = (payload_data.get("prompt")
                       or SkillsLibrary.enrich_product_prompt(payload_data["product"]))
            payload = self.request_filter.build_text_payload(prompt, model_tag)

        elif "menu_text" in payload_data:
            # We don't pass the profile into this prompt
            prompt  = SkillsLibrary.analyse_menu_prompt(payload_data["menu_text"])
            payload = self.request_filter.build_text_payload(prompt, model_tag)

        elif "text" in payload_data:
            prompt  = SkillsLibrary.analyze_text_prompt(payload_data["text"], profile)
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
        # --- DEBUG WIRE INJECTION ---
        import json
        print(f"\n{'='*60}")
        print(f"📡 [DEBUG] ROUTING TO: {self.provider_name} ({model_tag})")
        print(f"📦 [DEBUG] HEADERS:")
        for k, v in headers.items():
            # Mask API keys so you can safely paste the logs
            if "Bearer" in str(v):
                v = str(v)[:15] + "...[MASKED]"
            if "x-api-key" in str(k).lower() or "key" in str(k).lower():
                v = str(v)[:8] + "...[MASKED]"
            print(f"   {k}: {v}")
        print(f"📄 [DEBUG] FULL PAYLOAD:")
        print(json.dumps(payload, indent=2))
        print(f"{'='*60}\n")
        # ----------------------------
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
                        raise LLMError.server_error(
                            self.provider_name, response.status, resp_text[:200]
                        )

        except asyncio.TimeoutError:
            raise LLMError.network_timeout(self.provider_name)
        except aiohttp.ClientError as e:
            raise LLMError.network_failure(self.provider_name, str(e))
