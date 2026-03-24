# core/llm/providers/gemini.py
import os
from .base import BaseProvider
from ..filters import GeminiRequestFilter, GeminiResponseFilter
from ..config import config
from ..probe import GeminiProbeMixin


class GeminiProvider(GeminiProbeMixin, BaseProvider):

    @property
    def provider_name(self) -> str:
        return "Gemini"

    @property
    def default_model(self) -> str:
        return config.primary_model("gemini")

    @property
    def request_filter(self):
        return GeminiRequestFilter()

    @property
    def response_filter(self):
        return GeminiResponseFilter()

    def build_url(self, model_tag: str) -> str:
        key = os.environ.get("GEMINI_API_KEY", "")
        tag = config.resolve(model_tag, "gemini")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{tag}:generateContent?key={key}"

    def headers(self) -> dict:
        return {}
