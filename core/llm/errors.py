# core/llm/errors.py
"""
Normalised LLM error contract.

All provider-specific HTTP errors are translated into LLMError subclasses
before leaving the provider layer. The router and callers never see a
GeminiError, ClaudeError, or OpenAIError — only these cases.

This mirrors the LLMError enum in LLMConnector.swift, using @classmethod
factories to replicate Swift's enum associated values pattern in Python.

Usage:
    raise LLMError.rate_limited("Gemini", retry_after=30)
    raise LLMError.decode_failed("Claude", raw=response_text)

    try:
        ...
    except LLMError as e:
        if e.is_failoverable:
            # try next provider
        else:
            raise
"""


class LLMError(Exception):
    """
    Base exception for all normalised LLM errors.

    Attributes:
        provider (str):          The provider name that raised the error.
        is_failoverable (bool):  True if the router should try the next
                                 provider in the failover chain. False for
                                 errors that indicate a bad request or
                                 misconfiguration that retrying won't fix.
        raw_response (str|None): The raw response body for decode failures.
                                 None for all other error types.
    """

    def __init__(self, message: str, provider: str = "Unknown",
                 is_failoverable: bool = True):
        super().__init__(message)
        self.provider        = provider
        self.is_failoverable = is_failoverable
        self.raw_response    = None   # populated by decode_failed only

    # ------------------------------------------------------------------ #
    # Failoverable errors — router will try the next provider             #
    # ------------------------------------------------------------------ #

    @classmethod
    def rate_limited(cls, provider: str, retry_after: int = None) -> "LLMError":
        """Provider returned HTTP 429 or equivalent quota exhaustion."""
        suffix = f" Retry after {retry_after}s." if retry_after else ""
        return cls(f"{provider} rate limited.{suffix}",
                   provider=provider, is_failoverable=True)

    @classmethod
    def server_error(cls, provider: str, status_code: int,
                     message: str) -> "LLMError":
        """Provider returned HTTP 5xx."""
        return cls(f"{provider} server error {status_code}: {message}",
                   provider=provider, is_failoverable=True)

    @classmethod
    def network_timeout(cls, provider: str) -> "LLMError":
        """Request to provider timed out."""
        return cls(f"{provider} timed out.",
                   provider=provider, is_failoverable=True)

    @classmethod
    def network_failure(cls, provider: str, underlying: str) -> "LLMError":
        """Network-level failure (DNS, connection refused, etc.)."""
        return cls(f"{provider} network failure: {underlying}",
                   provider=provider, is_failoverable=True)

    @classmethod
    def decode_failed(cls, provider: str, raw: str = None) -> "LLMError":
        """
        Provider returned a response that could not be parsed as AnalysisResult.

        The raw response string is attached to the instance for debugging
        but intentionally excluded from the exception message to prevent
        large payloads bloating log aggregators.
        """
        err = cls(f"{provider} returned an unreadable response.",
                  provider=provider, is_failoverable=True)
        err.raw_response = raw
        return err

    @classmethod
    def empty_response(cls, provider: str) -> "LLMError":
        """Provider returned a structurally valid response with no content."""
        return cls(f"{provider} returned an empty response.",
                   provider=provider, is_failoverable=True)

    # ------------------------------------------------------------------ #
    # Non-failoverable errors — retrying will not help                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def invalid_request(cls, reason: str) -> "LLMError":
        """The request payload is malformed. Do not retry."""
        return cls(f"Invalid request: {reason}",
                   provider="Unknown", is_failoverable=False)

    @classmethod
    def encoding_failed(cls, reason: str) -> "LLMError":
        """Image or payload encoding failed before the request was sent."""
        return cls(f"Encoding failed: {reason}",
                   provider="Unknown", is_failoverable=False)

    @classmethod
    def all_providers_failed(cls, providers: list) -> "LLMError":
        """Every provider in the failover chain was exhausted."""
        return cls(f"All providers failed: {', '.join(providers)}",
                   provider="Router", is_failoverable=False)

    @classmethod
    def no_providers_configured(cls) -> "LLMError":
        """The request specified no providers."""
        return cls("No LLM providers configured.",
                   provider="Router", is_failoverable=False)

    @classmethod
    def unknown_provider(cls, provider: str) -> "LLMError":
        """The requested provider name is not in the registry."""
        return cls(f"Unknown provider: {provider}",
                   provider=provider, is_failoverable=False)
