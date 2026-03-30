#!/usr/bin/env python3
# scripts/probe_live.py
"""
Live provider connectivity diagnostic.

Hits each provider's real API to verify:
  - API key is valid and accepted
  - Model list endpoint is reachable
  - Configured default model exists in the live catalogue
  - Round-trip latency
  - Remaining API credit (where exposed via response headers)

NOT a unit test — requires real API keys set in environment.
Run on demand before a session to confirm provider health.

Usage:
    python scripts/probe_live.py all                # probe all providers
    python scripts/probe_live.py gemini             # probe one provider
    python scripts/probe_live.py gemini claude      # probe subset
    python scripts/probe_live.py                    # show this help
"""

import asyncio
import sys
import os

# Allow running from project root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm.providers.gemini import GeminiProvider
from core.llm.providers.claude import ClaudeProvider
from core.llm.providers.openai import OpenAIProvider
from core.llm.probe import ProviderStatus


PROVIDERS = {
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
}


def check_env_keys() -> dict[str, bool]:
    """Check which API keys are present in the environment."""
    keys = {
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "claude": bool(os.environ.get("CLAUDE_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
    }
    return keys


async def probe_provider(name: str, provider) -> ProviderStatus:
    print(f"  🔍 Probing {name}...")
    status = await provider.probe()
    return status

def print_status(status: ProviderStatus) -> None:
    icon    = "✅" if status.is_reachable else "❌"
    credit  = f"  credit_remaining={status.credit_remaining}" if status.credit_remaining is not None else ""
    valid   = "✓ default model found" if status.default_model_valid else "✗ DEFAULT MODEL NOT IN CATALOGUE"
    error   = f"  error={status.error}" if status.error else ""

    print(f"\n  {icon} {status.provider}")
    print(f"     latency       : {status.latency_ms}ms")
    print(f"     default model : {valid}")
    print(f"     models found  : {len(status.available_models)}")
    
    # Show the complete list in the order provided by the backend
    if status.available_models:
        for m in status.available_models:
            print(f"       - {m}")
 
    if credit:
        print(f"     {credit.strip()}")
    if error:
        print(f"     {error.strip()}")

async def main(targets: list[str]) -> None:
    print("\n── Live Provider Probe ─────────────────────────────────────")
    print(f"   Targets: {', '.join(targets)}\n")

    # Check keys first
    keys = check_env_keys()
    for name in targets:
        if not keys.get(name, True):  # unknown providers pass through
            print(f"  ⚠️  {name}: no API key found in environment — skipping")

    print()

    results: list[ProviderStatus] = []
    for name in targets:
        if not keys.get(name, True):
            continue
        if name not in PROVIDERS:
            print(f"  ⚠️  Unknown provider '{name}' — skipping")
            continue
        provider = PROVIDERS[name]()
        status   = await probe_provider(name, provider)
        results.append(status)
        print_status(status)

    # Summary
    print("\n── Summary ─────────────────────────────────────────────────")
    for s in results:
        icon = "✅" if s.is_reachable else "❌"
        credit = f"  credit={s.credit_remaining}" if s.credit_remaining is not None else ""
        print(f"  {icon} {s.provider:<10} {s.latency_ms:>5}ms  {len(s.available_models):>3} models{credit}")

    unreachable = [s for s in results if not s.is_reachable]
    if unreachable:
        print(f"\n  ⚠️  {len(unreachable)} provider(s) unreachable: "
              f"{', '.join(s.provider for s in unreachable)}")
    else:
        print(f"\n  All {len(results)} provider(s) healthy.")
    print()


HELP = (
    "usage: probe_live.py <target> [target ...]\n"
    "\n"
    "targets:\n"
    "  all              probe all configured providers\n"
    "  gemini           probe Gemini only\n"
    "  claude           probe Claude only\n"
    "  openai           probe OpenAI only\n"
    "  gemini claude    probe a subset (space-separated)\n"
    "\n"
    "examples:\n"
    "  ./scripts/probe_live.py all\n"
    "  ./scripts/probe_live.py gemini claude\n"
    "  ./scripts/probe_live.py openai"
)

if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]

    if not args:
        print(HELP)
        sys.exit(0)

    if args == ["all"]:
        requested = list(PROVIDERS.keys())
    else:
        requested = args

    asyncio.run(main(requested))
