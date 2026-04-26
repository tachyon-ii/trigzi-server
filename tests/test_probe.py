"""
=============================================================================
Module:        Test — Provider Probe / Health Check
Location:      tests/test_probe.py
Description:   Unit tests for core/llm/probe.py — the provider health
               check layer that periodically pings each LLM provider
               to track availability, default-model presence, and
               response latency. All network calls are mocked; no
               real API keys required.

Architecture Note:
The probe layer is what feeds the router's "is this provider up?"
signal. If it lies (false-positive on a dead provider, or
false-negative on a healthy one), the router either piles requests
onto a failing API or unnecessarily skips a working one. These
tests cover the four provider implementations (Gemini, Claude,
OpenAI, plus the scheduler that orchestrates them) against
controlled mock responses representing each upstream's success
and failure modes.
=============================================================================
"""

# Test files use different conventions to library code; pylint relaxations:
#   missing-class-docstring  — test class names ARE the docstring
#   missing-function-docstring — test method names ARE the docstring
#   import-outside-toplevel — methods import lazily to scope mock.patch / defer slow loads
#   redefined-outer-name   — pytest fixture pattern: fixture & param share name
#   unused-argument        — Mock side_effect callbacks take *args, **kwargs they don't read
# pylint: disable=missing-class-docstring,missing-function-docstring,import-outside-toplevel,redefined-outer-name,unused-argument

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm.probe import (
    ProviderStatus,
    ProbeScheduler,
    init_scheduler,
)
from core.llm.providers.gemini import GeminiProvider
from core.llm.providers.claude import ClaudeProvider
from core.llm.providers.openai import OpenAIProvider


# MARK: - Global Mock Constants

# By defining these here, the mock payloads and assertions can never drift out of sync.
GEMINI_EXPECTED = ["gemini-2.5-flash", "gemini-2.5-pro"]
GEMINI_MOCK_PAYLOAD = {
    "models": [
        {
            "name": f"models/{m}",
            "supportedGenerationMethods": ["generateContent"]
        }
        for m in GEMINI_EXPECTED
    ]
}

CLAUDE_EXPECTED = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
CLAUDE_MOCK_PAYLOAD = {"data": [{"id": m} for m in CLAUDE_EXPECTED]}

OPENAI_EXPECTED = ["gpt-4o", "gpt-4o-mini", "o3"]
# OpenAI returns a 'created' unix timestamp per model — the probe layer now
# uses this to populate ProviderStatus.model_metadata. The values below are
# arbitrary but deterministic so test assertions can verify the mapping.
OPENAI_CREATED = {
    "gpt-4o":      1715472000,  # 2024-05-12 (UTC)
    "gpt-4o-mini": 1721260800,  # 2024-07-18 (UTC)
    "o3":          1744156800,  # 2025-04-09 (UTC)
}
OPENAI_MOCK_PAYLOAD = {
    "data": [{"id": m, "created": OPENAI_CREATED[m]} for m in OPENAI_EXPECTED]
}


# MARK: - Shared mock helpers

def make_mock_response(status: int, json_data: dict, headers: dict = None):
    """Build an aiohttp-style async context manager mock."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json   = AsyncMock(return_value=json_data)
    mock_resp.headers = headers or {}

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__  = AsyncMock(return_value=False)
    return cm, mock_resp


def make_mock_session(response_cm):
    """Build a mock aiohttp.ClientSession."""
    session = AsyncMock()
    session.get = MagicMock(return_value=response_cm)
    session_cm  = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__  = AsyncMock(return_value=False)
    return session_cm


# MARK: - ProviderStatus tests

class TestProviderStatus(unittest.TestCase):

    def test_str_reachable(self):
        s = ProviderStatus(
            provider="Gemini", is_reachable=True, latency_ms=120,
            available_models=GEMINI_EXPECTED,
            default_model_valid=True, credit_remaining=950
        )
        text = str(s)
        self.assertIn("✅", text)
        self.assertIn("Gemini", text)
        self.assertIn("120ms", text)
        self.assertIn("credit=950", text)

    def test_str_unreachable(self):
        s = ProviderStatus(
            provider="Claude", is_reachable=False, latency_ms=5000,
            available_models=[], default_model_valid=False,
            credit_remaining=None, error="Timeout"
        )
        text = str(s)
        self.assertIn("❌", text)
        self.assertIn("Claude", text)
        self.assertNotIn("credit=", text)

    def test_default_model_valid_flag(self):
        s = ProviderStatus(
            provider="OpenAI", is_reachable=True, latency_ms=200,
            available_models=OPENAI_EXPECTED,
            default_model_valid=False, credit_remaining=None
        )
        self.assertIn("✗", str(s))


# MARK: - Gemini probe tests

class TestGeminiProbe(unittest.IsolatedAsyncioTestCase):

    async def test_successful_probe(self):
        """Gemini probe extracts model names and strips 'models/' prefix.

        Gemini does not expose remaining-quota in successful response headers
        (quota info comes back only inside 429 error bodies as structured
        QuotaFailure details). So credit_remaining is expected to be None
        on a successful probe and is not asserted here.
        """
        resp_cm, _ = make_mock_response(200, GEMINI_MOCK_PAYLOAD)
        session_cm = make_mock_session(resp_cm)

        provider = GeminiProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertTrue(status.is_reachable)
        for expected_model in GEMINI_EXPECTED:
            self.assertIn(expected_model, status.available_models)
        self.assertGreaterEqual(status.latency_ms, 0)

    async def test_default_model_validated(self):
        """Probe confirms the configured default model is in the available list."""
        resp_cm, _ = make_mock_response(200, GEMINI_MOCK_PAYLOAD)
        session_cm = make_mock_session(resp_cm)

        provider = GeminiProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertTrue(status.default_model_valid)

    async def test_http_error_marks_unreachable(self):
        """A non-200 response marks the provider as unreachable."""
        resp_cm, _ = make_mock_response(403, {"error": "forbidden"})
        session_cm = make_mock_session(resp_cm)

        provider = GeminiProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertFalse(status.is_reachable)
        self.assertEqual(status.available_models, [])
        self.assertIsNotNone(status.error)

    async def test_timeout_marks_unreachable(self):
        """A network timeout marks the provider as unreachable."""
        session_cm = AsyncMock()
        session_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        session_cm.__aexit__  = AsyncMock(return_value=False)

        provider = GeminiProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertFalse(status.is_reachable)
        self.assertEqual(status.error, "Timeout")


# MARK: - Claude probe tests

class TestClaudeProbe(unittest.IsolatedAsyncioTestCase):

    async def test_successful_probe(self):
        """Claude probe uses 'id' key and reads anthropic credit header."""
        headers = {"anthropic-ratelimit-requests-remaining": "490"}
        resp_cm, _ = make_mock_response(200, CLAUDE_MOCK_PAYLOAD, headers)
        session_cm = make_mock_session(resp_cm)

        provider = ClaudeProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertTrue(status.is_reachable)
        for expected_model in CLAUDE_EXPECTED:
            self.assertIn(expected_model, status.available_models)
        self.assertEqual(status.credit_remaining, 490)

    async def test_invalid_api_key(self):
        """A 401 marks Claude as unreachable."""
        resp_cm, _ = make_mock_response(401, {"type": "error", "error": {"type": "authentication_error"}})
        session_cm = make_mock_session(resp_cm)

        provider = ClaudeProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertFalse(status.is_reachable)


# MARK: - OpenAI probe tests

class TestOpenAIProbe(unittest.IsolatedAsyncioTestCase):

    async def test_successful_probe(self):
        """OpenAI probe returns clean model identifiers and reads ratelimit header."""
        headers = {"x-ratelimit-remaining-requests": "199"}
        resp_cm, _ = make_mock_response(200, OPENAI_MOCK_PAYLOAD, headers)
        session_cm = make_mock_session(resp_cm)

        provider = OpenAIProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertTrue(status.is_reachable)
        # As of the data/presentation cleanup (April 2026), available_models
        # contains clean identifiers — no padding, no [date] suffix.
        for expected_model in OPENAI_EXPECTED:
            self.assertIn(expected_model, status.available_models)
        self.assertEqual(status.credit_remaining, 199)

    async def test_model_metadata_populated(self):
        """OpenAI probe exposes per-model `created_at` via model_metadata."""
        resp_cm, _ = make_mock_response(200, OPENAI_MOCK_PAYLOAD)
        session_cm = make_mock_session(resp_cm)

        provider = OpenAIProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        # Every model in the response should have a metadata entry.
        for expected_model in OPENAI_EXPECTED:
            self.assertIn(expected_model, status.model_metadata)
            entry = status.model_metadata[expected_model]
            self.assertIn("created_at", entry)
            # Format: YYYY-MM-DD
            self.assertRegex(entry["created_at"], r"^\d{4}-\d{2}-\d{2}$")

        # Spot-check a known mapping (2024-05-12 from OPENAI_CREATED).
        self.assertEqual(
            status.model_metadata["gpt-4o"]["created_at"],
            "2024-05-12"
        )

    async def test_other_providers_have_empty_metadata(self):
        """Gemini and Claude don't expose per-model dates, so model_metadata is {}."""
        resp_cm, _ = make_mock_response(200, GEMINI_MOCK_PAYLOAD)
        session_cm = make_mock_session(resp_cm)

        provider = GeminiProvider()
        with patch("aiohttp.ClientSession", return_value=session_cm):
            status = await provider.probe()

        self.assertEqual(status.model_metadata, {})


# MARK: - ProbeScheduler tests

class TestProbeScheduler(unittest.IsolatedAsyncioTestCase):

    def _make_registry(self):
        """Registry with mock providers that return canned ProviderStatus."""
        gemini = GeminiProvider()
        claude = ClaudeProvider()
        gemini.probe = AsyncMock(return_value=ProviderStatus(
            provider="Gemini", is_reachable=True, latency_ms=100,
            available_models=GEMINI_EXPECTED, default_model_valid=True,
            credit_remaining=900
        ))
        claude.probe = AsyncMock(return_value=ProviderStatus(
            provider="Claude", is_reachable=False, latency_ms=5000,
            available_models=[], default_model_valid=False,
            credit_remaining=None, error="Timeout"
        ))
        return {"gemini": gemini, "claude": claude}

    async def test_start_probes_all_providers(self):
        """start() calls probe() on every registered provider."""
        registry  = self._make_registry()
        scheduler = ProbeScheduler(registry, interval_s=9999)
        await scheduler.start()
        scheduler.stop()

        registry["gemini"].probe.assert_called_once()
        registry["claude"].probe.assert_called_once()

    async def test_status_cached_after_start(self):
        """status() returns the cached result after start()."""
        registry  = self._make_registry()
        scheduler = ProbeScheduler(registry, interval_s=9999)
        await scheduler.start()
        scheduler.stop()

        gemini_status = scheduler.status("gemini")
        self.assertIsNotNone(gemini_status)
        self.assertTrue(gemini_status.is_reachable)
        self.assertEqual(gemini_status.credit_remaining, 900)

    async def test_all_status_returns_full_picture(self):
        """all_status() returns a dict with an entry per provider."""
        registry  = self._make_registry()
        scheduler = ProbeScheduler(registry, interval_s=9999)
        await scheduler.start()
        scheduler.stop()

        all_s = scheduler.all_status()
        self.assertIn("gemini", all_s)
        self.assertIn("claude", all_s)
        self.assertFalse(all_s["claude"].is_reachable)

    async def test_init_scheduler_sets_module_singleton(self):
        """init_scheduler() sets the module-level scheduler singleton."""
        import core.llm.probe as probe_module
        registry  = self._make_registry()
        scheduler = init_scheduler(registry, interval_s=9999)
        self.assertIs(probe_module.scheduler, scheduler)


if __name__ == "__main__":
    unittest.main()
