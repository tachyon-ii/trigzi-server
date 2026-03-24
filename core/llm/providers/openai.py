# core/llm/providers/openai.py
import os
from .base import BaseProvider
from ..filters import OpenAIRequestFilter, OpenAIResponseFilter
from ..config import config
from ..probe import OpenAIProbeMixin


class OpenAIProvider(OpenAIProbeMixin, BaseProvider):

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
