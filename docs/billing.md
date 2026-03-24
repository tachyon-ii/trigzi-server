# Billing & Credit Architecture Notes
## Multi-Provider Gateway (Trigzi / barcode-api)
### As of early 2026

---

## Provider-by-Provider API Status

### 1. OpenAI — Formal Usage & Costs API (launched late 2025)

Designed for programmatic integration, not dashboard hacks.

**Costs endpoint:**
```
GET https://api.openai.com/v1/organization/costs
Params: start_time, end_time, group_by=line_item
```

**Usage endpoint (token metrics per minute/hour/day):**
```
GET https://api.openai.com/v1/organization/usage/completions
(also: /images, /audio)
```

**Prepay balance (credit grants):**
```
GET https://api.openai.com/v1/dashboard/billing/credit_grants
Requires: Organization ID + key with billing permissions
```

---

### 2. Claude (Anthropic) — Header-Driven, No Balance API

No standalone balance endpoint. All capacity data comes from
response headers on every successful API call.

Headers to extract in `BaseProvider.analyze()`:

| Header | Meaning |
|--------|---------|
| `anthropic-ratelimit-requests-remaining` | Requests left in window |
| `anthropic-ratelimit-tokens-remaining` | Tokens left in window |
| `anthropic-ratelimit-tokens-limit` | Total token limit |
| `anthropic-ratelimit-tokens-reset` | RFC 3339 reset timestamp |

---

### 3. Gemini — No Credit API (AI Studio) / Cloud Billing (Vertex)

- **AI Studio**: No programmatic credit endpoint. Monitor via
  Google AI Studio Spend Tab only.
- **Vertex AI**: Use standard Cloud Billing API:
  ```
  GET https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo
  ```

---

## Recommended Implementation: Reactive Telemetry Sink

Avoid polling billing APIs on every request. Use three layers:

### Layer 1 — Response Interceptor (in BaseProvider.analyze)
Capture rate-limit headers from every response and write to a
centralised cache (SQLite / Redis). Zero added latency to the
request path — fire-and-forget write.

### Layer 2 — Scheduled Poller (background task)
- Hits OpenAI Costs API once per hour to update global spend telemetry
- Lives in `consensus_worker.py` or a dedicated `billing_worker.py`
- Does NOT block request handling

### Layer 3 — Circuit Breaker (in LLMRouter)
- Reads `anthropic-ratelimit-tokens-remaining` from cache
- Preemptively marks Claude as degraded before the 429 hits
- Router then routes to next provider in hierarchy
- Prevents cascading failures and wasted retry latency

---

## Implementation Priorities for Trigzi

1. **Immediate** — Extract Claude headers in `BaseProvider.analyze()`
   and write to `LLMCallRecord`. Already partially wired via
   `_extract_credit()` in `probe.py` — extend to analyze path.

2. **Short term** — OpenAI credit_grants poller as a scheduled task.
   Runs hourly, writes to a `BillingSnapshot` SwiftData/SQLite record.

3. **Medium term** — Circuit breaker in `LLMRouter._resolve_mode()`.
   Check cached token headroom before routing. If Claude is below
   threshold, skip it in the hierarchy without waiting for a 429.

4. **Dashboard** — Expose `/admin/billing` Flask endpoint that reads
   from the cache and returns a unified view across all providers.
   No per-request API calls — reads cache only.

---

## Notes

- Gemini has no credit API at AI Studio tier — log model-level
  latency and error rates as a proxy for quota pressure.
- The `anthropic-ratelimit-tokens-reset` RFC 3339 timestamp is
  the most useful Claude field — it tells you exactly when to
  retry rather than requiring exponential backoff guessing.
- OpenAI's new Usage API requires Organization-level API keys,
  not project keys — document this in `.env.example`.
