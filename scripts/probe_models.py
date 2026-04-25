#!/usr/bin/env python3
"""
=============================================================================
Module:        Model Hierarchy Builder
Location:      scripts/probe_models.py
Description:   Probes the live model-list endpoints of all configured providers
               (Gemini, Claude, OpenAI) and writes a structured ranking to
               data/model_hierarchy.json.

               The hierarchy is consumed by:
                 - tests/test_sigmund_model_hierarchy.py (and any future
                   test asserting "task X uses a stronger model than task Y")
                 - any future runtime code that wants to make routing
                   decisions based on relative model strength

Why this exists:
               Embedding the ranking inside a test file (the v2 approach)
               coupled the data to a single consumer and required hand-edits
               every time a new model shipped. This script regenerates the
               ranking from the providers themselves, which is the source of
               truth for what models exist.

Usage:
               ./scripts/probe_models.py                    # dry-run, prints to stdout
               ./scripts/probe_models.py --write            # writes data/model_hierarchy.json
               ./scripts/probe_models.py --write --verbose  # also lists skipped models

Failure mode:
               Aborts with non-zero exit code if the probe encounters a model
               name that does not match any tier_pattern. This is intentional:
               an unknown model is a human-intervention event, not something
               the probe should silently default-rank. When (e.g.) Anthropic
               ships claude-opus-4-8, this script will refuse to write the
               hierarchy until a human has decided where it sits.

               To unblock: add a tier_pattern to TIER_PATTERNS below, or add
               an explicit override to MANUAL_OVERRIDES.

=============================================================================
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_THIS_DIR    = Path(__file__).resolve().parent
_PROJECT_DIR = _THIS_DIR.parent
_OUTPUT_PATH = _PROJECT_DIR / "data" / "model_hierarchy.json"

# Make core/ importable
sys.path.insert(0, str(_PROJECT_DIR))


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------
# Patterns are evaluated in order; first match wins. Tier_base is the floor
# fidelity for the family — version numbers within the name produce the
# point-fidelity offset that makes claude-opus-4-7 outrank claude-opus-4-6
# automatically.
#
# Patterns deliberately use the simplest regex that distinguishes the tier.
# More specific patterns must come BEFORE less specific patterns (e.g. mini
# variants must be matched before the bare family pattern).
#
# Adding support for a new family:
#   1. Pick a tier_base (5/4/3/2/1 system, * 1000 to leave room for offsets)
#   2. Insert pattern in correct precedence order
#   3. Run with --verbose to confirm it classifies as expected
# ---------------------------------------------------------------------------

TIER_FRONTIER = 5000   # opus, gpt-pro, gemini-pro, o3, o1-pro
TIER_FLAGSHIP = 4000   # sonnet, gpt-5.x full, gpt-4o, gpt-4.1, gpt-4-turbo
TIER_FAST     = 3000   # haiku, gpt-x-mini, o4-mini, o3-mini, gemini-flash
TIER_UTILITY  = 2000   # gpt-4o-mini, gpt-x-nano, gpt-4.1-nano, flash-lite
TIER_LEGACY   = 1000   # gpt-3.5-turbo, gpt-4 base


class TierRule(NamedTuple):
    """A pattern that maps a model name to a tier."""
    pattern:    re.Pattern
    tier_base:  int
    label:      str   # human-readable tier name for the JSON output


TIER_PATTERNS: list[TierRule] = [
    # ── Frontier ────────────────────────────────────────────────────────
    TierRule(re.compile(r"^claude-opus-"),                  TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^gpt-\d+(\.\d+)?-pro(-|$)"),      TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^gpt-\d+-pro(-|$)"),              TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^o1-pro"),                        TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^o1(-\d|$)"),                     TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^o3(-|$)(?!mini)"),               TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^gemini.*-pro(-|$)"),             TIER_FRONTIER, "frontier"),
    TierRule(re.compile(r"^gemini-pro-latest$"),            TIER_FRONTIER, "frontier"),

    # ── Utility (matched BEFORE flagship, otherwise gpt-4o-mini → flagship) ──
    TierRule(re.compile(r"-nano(-|$)"),                     TIER_UTILITY,  "utility"),
    TierRule(re.compile(r"^gpt-4o-mini(-|$)"),              TIER_UTILITY,  "utility"),
    TierRule(re.compile(r"-flash-lite(-|$)"),               TIER_UTILITY,  "utility"),
    TierRule(re.compile(r"flash-lite-latest$"),             TIER_UTILITY,  "utility"),

    # ── Fast / mini reasoning (BEFORE flagship for same reason as above) ──
    TierRule(re.compile(r"^claude-haiku-"),                 TIER_FAST,     "fast"),
    TierRule(re.compile(r"-mini(-|$)"),                     TIER_FAST,     "fast"),
    TierRule(re.compile(r"^o4-mini"),                       TIER_FAST,     "fast"),
    TierRule(re.compile(r"^o3-mini"),                       TIER_FAST,     "fast"),
    TierRule(re.compile(r"^gemini.*-flash(-|$)(?!lite)"),   TIER_FAST,     "fast"),
    TierRule(re.compile(r"^gemini-flash-latest$"),          TIER_FAST,     "fast"),

    # ── Flagship ────────────────────────────────────────────────────────
    TierRule(re.compile(r"^claude-sonnet-"),                TIER_FLAGSHIP, "flagship"),
    TierRule(re.compile(r"^gpt-5(\.\d+)?(-\d{4}-\d{2}-\d{2})?$"),
                                                            TIER_FLAGSHIP, "flagship"),
    TierRule(re.compile(r"^gpt-4o(-\d{4}-\d{2}-\d{2})?$"),  TIER_FLAGSHIP, "flagship"),
    TierRule(re.compile(r"^gpt-4\.1(-\d{4}-\d{2}-\d{2})?$"),TIER_FLAGSHIP, "flagship"),
    TierRule(re.compile(r"^gpt-4-turbo"),                   TIER_FLAGSHIP, "flagship"),

    # ── Legacy ──────────────────────────────────────────────────────────
    TierRule(re.compile(r"^gpt-3\.5-"),                     TIER_LEGACY,   "legacy"),
    TierRule(re.compile(r"^gpt-4(-\d{4})?$"),               TIER_LEGACY,   "legacy"),
    TierRule(re.compile(r"^gpt-4-0613$"),                   TIER_LEGACY,   "legacy"),
]


# ---------------------------------------------------------------------------
# Skip patterns — non-chat endpoints that shouldn't appear in any chat
# task's model list. Matched models are written to the "skipped" section
# of the JSON for visibility but excluded from the rankable hierarchy.
# ---------------------------------------------------------------------------

SKIP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^text-embedding-"),       "embedding"),
    (re.compile(r"-embedding"),             "embedding"),
    (re.compile(r"text-embedding-ada"),     "embedding"),
    (re.compile(r"^tts-"),                  "audio"),
    (re.compile(r"-tts(-|$)"),              "audio"),
    (re.compile(r"^whisper-"),              "audio"),
    (re.compile(r"-audio(-|$)"),            "audio"),
    (re.compile(r"-transcribe"),            "audio"),
    (re.compile(r"^dall-e-"),               "image"),
    (re.compile(r"-image(-|$)"),            "image"),
    (re.compile(r"^gpt-image-"),            "image"),
    (re.compile(r"^chatgpt-image-"),        "image"),
    (re.compile(r"-realtime(-|$)"),         "realtime"),
    (re.compile(r"^sora-"),                 "video"),
    (re.compile(r"-search-preview"),        "search"),
    (re.compile(r"-search-api"),            "search"),
    (re.compile(r"-deep-research"),         "research"),
    (re.compile(r"^omni-moderation"),       "moderation"),
    (re.compile(r"-codex"),                 "codex"),
    (re.compile(r"^babbage-"),              "legacy_completion"),
    (re.compile(r"^davinci-"),              "legacy_completion"),
    (re.compile(r"-instruct"),              "legacy_completion"),
    (re.compile(r"^gemma-"),                "open_weights"),
    (re.compile(r"^gpt-5\.\d+-chat-latest$"),"chat_latest_alias"),  # rolling pointer
    (re.compile(r"^gpt-5-chat-latest$"),    "chat_latest_alias"),
]


# ---------------------------------------------------------------------------
# Manual overrides — for cases the regex can't reasonably handle. Empty by
# design: the moment this dict needs entries, the patterns above probably
# need refinement instead.
# ---------------------------------------------------------------------------

MANUAL_OVERRIDES: dict[str, dict] = {
    # "some-weird-model": {"tier": TIER_FLAGSHIP, "label": "flagship", "fidelity": 4042},
}


# ---------------------------------------------------------------------------
# Version parsing — extracts the (family, point, dated_suffix) triple from a
# model name. The parser is deliberately conservative; if it can't find a
# version, point=0.
# ---------------------------------------------------------------------------

# Matches: -4-7, -4-6, -2.5-, -4.1-, -5.4- etc.
# Groups: family (int), point (int or None)
_VERSION_RE = re.compile(
    r"-(\d+)"          # family number (e.g. 4 in claude-opus-4-7)
    r"(?:[-.](\d+))?"  # optional point number (e.g. 7 in claude-opus-4-7)
)

# Matches a YYYYMMDD or YYYY-MM-DD suffix
_DATED_RE = re.compile(r"-?(\d{4})-?(\d{2})-?(\d{2})$")

# Matches a -preview suffix — these rank just below the stable equivalent
_PREVIEW_RE = re.compile(r"-preview$")


def _parse_version(name: str) -> tuple[int, int, bool, bool]:
    """
    Returns (family, point, is_dated, is_preview).

    Strips off skipped suffixes (dated, preview) before extracting the
    leading family.point pair.
    """
    is_preview = bool(_PREVIEW_RE.search(name))
    is_dated   = bool(_DATED_RE.search(name))

    # Strip dated suffix to avoid the parser grabbing the year as a family number
    stripped = name
    stripped = _DATED_RE.sub("", stripped)
    stripped = _PREVIEW_RE.sub("", stripped)

    m = _VERSION_RE.search(stripped)
    if not m:
        return (0, 0, is_dated, is_preview)
    family = int(m.group(1))
    point  = int(m.group(2)) if m.group(2) else 0
    return (family, point, is_dated, is_preview)


def _classify(name: str) -> tuple[int, str] | None:
    """
    Find which tier a model belongs to. Returns (tier_base, tier_label) or
    None if no pattern matches. Manual overrides take precedence.
    """
    if name in MANUAL_OVERRIDES:
        ov = MANUAL_OVERRIDES[name]
        return (ov["tier"], ov["label"])

    for rule in TIER_PATTERNS:
        if rule.pattern.search(name):
            return (rule.tier_base, rule.label)
    return None


def _should_skip(name: str) -> str | None:
    """Returns the skip reason if the model should be excluded, else None."""
    for pat, reason in SKIP_PATTERNS:
        if pat.search(name):
            return reason
    return None


def _compute_fidelity(name: str, tier_base: int) -> int:
    """
    Combine tier with parsed version into a single integer score.

    Layout:
      tier_base    e.g. 5000  (opus)
      + family*10        +40  (claude-opus-4-7 → family=4)
      + point             +7  (claude-opus-4-7 → point=7)
      - dated_penalty     -1  (dated alias ranks just below moving alias)
      - preview_penalty   -1  (preview ranks just below stable)

    For claude-opus-4-7 → 5000 + 40 + 7 = 5047
    For claude-opus-4-6 → 5000 + 40 + 6 = 5046
    For claude-opus-4-5-20251101 → 5000 + 40 + 5 - 1 = 5044

    Within a tier this gives stable monotonic ordering. Across tiers, the
    1000-point gap means even an exotic family/point combination (claude-opus-9-9
    = 5099) can't accidentally outrank a frontier model below it.
    """
    family, point, is_dated, is_preview = _parse_version(name)
    score = tier_base + (family * 10) + point
    if is_dated:
        score -= 1
    if is_preview:
        score -= 1
    return score


# ---------------------------------------------------------------------------
# Provider probing
# ---------------------------------------------------------------------------

async def _gather_models() -> dict[str, list[str]]:
    """
    Use the existing ProbeMixin via core.llm.probe to enumerate models for
    every configured provider. Returns {provider_name: [model_name, ...]}.

    Mirrors the instantiation pattern in scripts/probe_live.py: providers
    take no constructor arguments and read their config from the module-level
    LLMProviderConfig singleton via the import side-effect. Also mirrors the
    env-key precheck so missing keys produce a clear diagnostic instead of
    a generic auth failure.

    As of the data/presentation cleanup in probe.py (April 2026), every
    provider returns clean model identifiers in `available_models`. No
    normalization or suffix stripping is needed here.
    """
    from core.llm.providers.gemini import GeminiProvider
    from core.llm.providers.claude import ClaudeProvider
    from core.llm.providers.openai import OpenAIProvider

    provider_classes = {
        "gemini": GeminiProvider,
        "claude": ClaudeProvider,
        "openai": OpenAIProvider,
    }
    env_keys = {
        "gemini": "GEMINI_API_KEY",
        "claude": "CLAUDE_API_KEY",
        "openai": "OPENAI_API_KEY",
    }

    results: dict[str, list[str]] = {}
    for name, cls in provider_classes.items():
        if not os.environ.get(env_keys[name]):
            print(f"⚠  {name}: {env_keys[name]} not in environment — skipping",
                  file=sys.stderr)
            results[name] = []
            continue
        try:
            p = cls()
            status = await p.probe(timeout_s=15.0)
            if status.is_reachable:
                results[name] = sorted(status.available_models)
            else:
                print(f"⚠  {name}: probe failed — {status.error}", file=sys.stderr)
                results[name] = []
        except Exception as e:
            print(f"⚠  {name}: probe raised — {e}", file=sys.stderr)
            results[name] = []
    return results


# ---------------------------------------------------------------------------
# Build the hierarchy
# ---------------------------------------------------------------------------

class BuildResult(NamedTuple):
    ranked:  dict[str, dict]    # name -> {tier, label, fidelity, ...}
    skipped: dict[str, dict]    # name -> {provider, reason}
    unknown: list[tuple[str, str]]  # [(provider, name), ...] — abort triggers


def _build(probe_results: dict[str, list[str]]) -> BuildResult:
    """
    Classify every probed model. Unknown models accumulate in `unknown` and
    cause the caller to abort before writing the JSON.
    """
    ranked:  dict[str, dict] = {}
    skipped: dict[str, dict] = {}
    unknown: list[tuple[str, str]] = []

    for provider, names in probe_results.items():
        for name in names:
            # 1. Skip non-chat endpoints
            skip_reason = _should_skip(name)
            if skip_reason:
                skipped[name] = {"provider": provider, "reason": skip_reason}
                continue

            # 2. Classify into a tier
            classification = _classify(name)
            if classification is None:
                unknown.append((provider, name))
                continue
            tier_base, tier_label = classification

            # 3. Compute fidelity
            fidelity = _compute_fidelity(name, tier_base)
            family, point, is_dated, is_preview = _parse_version(name)

            ranked[name] = {
                "provider":   provider,
                "tier":       tier_label,
                "fidelity":   fidelity,
                "family":     family,
                "point":      point,
                "dated":      is_dated,
                "preview":    is_preview,
            }

    return BuildResult(ranked=ranked, skipped=skipped, unknown=unknown)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3].strip())
    ap.add_argument("--write",   action="store_true",
                    help=f"Write output to {_OUTPUT_PATH.relative_to(_PROJECT_DIR)} (default: dry-run to stdout)")
    ap.add_argument("--verbose", action="store_true",
                    help="Also print the skipped models and their reasons")
    args = ap.parse_args()

    print(f"── Probing providers for available models ──", file=sys.stderr)
    probe_results = asyncio.run(_gather_models())

    total = sum(len(v) for v in probe_results.values())
    print(f"   Found {total} models across {len(probe_results)} providers", file=sys.stderr)

    result = _build(probe_results)

    # ABORT on any unknown model — per design, this requires human intervention
    if result.unknown:
        print("", file=sys.stderr)
        print(f"❌ ABORT: {len(result.unknown)} model(s) did not match any tier_pattern:", file=sys.stderr)
        for provider, name in result.unknown:
            print(f"     {provider:8s}  {name}", file=sys.stderr)
        print("", file=sys.stderr)
        print("   These models cannot be safely ranked without human judgement.", file=sys.stderr)
        print("   To unblock, either:", file=sys.stderr)
        print("     1. Add a TIER_PATTERNS entry in scripts/probe_models.py, or", file=sys.stderr)
        print("     2. Add a SKIP_PATTERNS entry if it's a non-chat endpoint, or", file=sys.stderr)
        print("     3. Add a MANUAL_OVERRIDES entry for one-off cases.", file=sys.stderr)
        return 2

    # Build the JSON document
    doc = {
        "_comment":      "Auto-generated by scripts/probe_models.py — do not hand-edit. Regenerate when new models ship.",
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "tiers": {
            "frontier": TIER_FRONTIER,
            "flagship": TIER_FLAGSHIP,
            "fast":     TIER_FAST,
            "utility":  TIER_UTILITY,
            "legacy":   TIER_LEGACY,
        },
        "models":  dict(sorted(result.ranked.items(),
                               key=lambda kv: (-kv[1]["fidelity"], kv[0]))),
        "skipped": dict(sorted(result.skipped.items())),
    }

    # Print summary to stderr
    by_tier: dict[str, int] = {}
    for info in result.ranked.values():
        by_tier[info["tier"]] = by_tier.get(info["tier"], 0) + 1
    print("", file=sys.stderr)
    print(f"── Hierarchy summary ──", file=sys.stderr)
    for tier in ("frontier", "flagship", "fast", "utility", "legacy"):
        print(f"   {tier:9s} {by_tier.get(tier, 0):3d} models", file=sys.stderr)
    print(f"   skipped   {len(result.skipped):3d} models (non-chat endpoints)", file=sys.stderr)

    if args.verbose:
        print("", file=sys.stderr)
        print(f"── Skipped models ──", file=sys.stderr)
        for name, info in sorted(result.skipped.items()):
            print(f"   {info['reason']:18s}  {info['provider']:8s}  {name}", file=sys.stderr)

    # Output
    if args.write:
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _OUTPUT_PATH.open("w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        print("", file=sys.stderr)
        print(f"✅ Wrote {_OUTPUT_PATH.relative_to(_PROJECT_DIR)} "
              f"({len(result.ranked)} ranked, {len(result.skipped)} skipped)",
              file=sys.stderr)
    else:
        print(json.dumps(doc, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
