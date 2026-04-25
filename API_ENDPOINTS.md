# Trigzi Backend — API Endpoints

Base URL: `https://trigzi.com`

> **Auth note:** the `Authorization: Bearer <token>` header is currently checked **only on `GET /api/v1/messages`**, where its presence (just the `Bearer ` prefix — the token itself is not validated) is required. All other endpoints accept any caller that can reach the upstream. Treat this as a known gap; full auth middleware is planned.

---

## Product

### `GET /api/v1/product/<gtin>`

Look up a product by barcode. Returns immediately if already enriched. Streams SSE if enrichment is needed.

**Path parameter:** `gtin` — 8–14 digit barcode string. Normalised to EAN-13 before lookup.

**Response — enriched (JSON):**
```json
{ "status": "complete", "product": { ... } }
```

**Response — unenriched (SSE stream):**
```text
event: progress
data: {"message": "Found Skim Milk by Black & Gold"}

event: progress
data: {"message": "Running latest analytics…"}

event: enriched
data: {"status": "complete", "product": { ... }}
```

On failure during streaming, the stream emits:
```text
event: error
data: {"message": "Analytics failed. Please try scanning again."}
```

**Response — not found (JSON):**
```json
{ "status": "not_found", "gtin": "9310077217814" }
```

| Status | Meaning |
|---|---|
| 200 | Enriched product returned as JSON |
| 200 SSE | Unenriched — stream in progress |
| 404 | GTIN not in database |
| 400 | Invalid barcode format (non-digit, or length outside 8–14) |

> SSE endpoint: nginx buffering is disabled for `/api/v1/product/`. Do not proxy-buffer this route.

---

### `POST /api/v1/analyse/product`

Analyse an unknown product from dual-camera OCR text. Called when a barcode scan returns 404 and the user captures front label + nutrition panel.

**Request:**
```json
{
  "gtin":           "9310077217814",
  "text_front":     "BLACK & GOLD AUSTRALIAN SKIM MILK...",
  "text_nutrition": "INGREDIENTS: Ultra Heat Treated Skim Milk..."
}
```

**Response:**
```json
{
  "status": "ok",
  "result": {
    "item": "Black & Gold Skim Milk",
    "verdict": "Safe",
    "summary": "Plain skim milk, no clinical concerns for your profile.",
    "warnings": "",
    "ingredients": "skim milk",
    "flagged": "",
    "reasoning": "Single ingredient. No restricted items present in your profile."
  }
}
```

The `result` keys follow the `analyse_text.txt` prompt contract — note `item` (not `dish`); that field is reserved for `/api/v1/analyse/meal`.

| Status | Meaning |
|---|---|
| 200 | Analysis complete |
| 400 | Missing or invalid payload, or missing `gtin` |
| 422 | Unprocessable OCR (LLM `Valid_Input: false`) |
| 500 | Internal Server Error |

Each call writes a copy of the OCR input to `logs/scans/<ts>_<gtin>_ocr.txt` for regression replay.

---

### `POST /api/v1/enrich/nutrition`

Extract missing nutrition data from an OCR'd nutrition panel and patch the product row in MariaDB. Called when a product exists but has null `nutrition_100g`.

**Request:**
```json
{
  "gtin":     "9310077217814",
  "ocr_text": "Energy 1570kJ, Protein 5.6g, Fat 17.3g..."
}
```

**Response:**
```json
{
  "energy_kj": 1570,
  "calories_kcal": 375,
  "protein_g": 5.6,
  "fat_total_g": 17.3,
  "fat_saturated_g": 10.6,
  "carbohydrates_g": 47.9,
  "sugars_g": 34.0,
  "fibre_g": null,
  "sodium_mg": 453
}
```

If the GTIN is not present in `products`, the extracted payload is still returned to the client; the DB patch is silently skipped (logged as a warning).

| Status | Meaning |
|---|---|
| 200 | Nutrition data extracted; DB patched if GTIN present |
| 400 | Missing `gtin` or `ocr_text` |
| 422 | Unprocessable OCR (LLM `Valid_Input: false`) |
| 500 | Internal Server Error |

---

## Meal & Menu Analysis

### `POST /api/v1/analyse/meal`

Analyse a photo of a plated meal against the user's dietary profile. Accepts raw base64-encoded JPEG or a Data URI (the backend strips any `data:image/...;base64,` prefix defensively).

**Request:**
```json
{
  "image":   "<base64 JPEG>",
  "profile": { "dietary_profile": { ... } }
}
```

**Response:**
```json
{
  "status": "ok",
  "result": {
    "dish": "Pad Thai",
    "verdict": "Caution",
    "summary": "Contains fish sauce and peanuts.",
    "warnings": "fish, peanuts",
    "ingredients": "noodles, egg, fish sauce, peanuts",
    "flagged": "fish sauce, peanuts",
    "reasoning": "Traditional preparation includes both fish sauce and crushed peanuts. Both are on your allergen list."
  }
}
```

| Status | Meaning |
|---|---|
| 200 | Analysis complete |
| 400 | Missing `image` field |
| 422 | Unprocessable image (LLM `Valid_Input: false` — non-food, blurred, or all-black) |
| 500 | Internal Server Error |

---

### `POST /api/v1/analyse/menu`

Analyse OCR-extracted restaurant menu text. Returns dish names with listed and suspected ingredients. Raw OCR scan is saved to `logs/scans/<ts>_menu_scan.txt` for regression testing.

**Request:**
```json
{
  "text": "OYSTERS | half dozen with apple, aniseed & sumac\nHOMMUS | chickpeas, brown butter..."
}
```

**Response:**
```json
{
  "status": "ok",
  "result": {
    "type": "menu",
    "items": [
      {
        "name": "OYSTERS",
        "listed_ingredients": ["apple", "aniseed", "sumac"],
        "suspected_ingredients": ["lemon juice", "shallot", "salt"]
      }
    ]
  }
}
```

| Status | Meaning |
|---|---|
| 200 | Analysis complete |
| 400 | Missing or empty `text` |
| 422 | Unprocessable text (LLM `Valid_Input: false` — not a menu) |
| 500 | Internal Server Error |

---

## Chat

### `POST /api/v1/chat/stream`

Main dietary chat assistant. Streams SSE asynchronously. Two-stage pipeline: (1) clinical response + optional UI action command, (2) emoji micro-inference appended as a separate event.

The `[ACTION: NONE]` sentinel emitted by the LLM is filtered out before reaching the client; only real actions become `action` events.

**Request:**
```json
{
  "message":         "Is the beetroot salad safe for me?",
  "system_context":  { "dietary_profile": { ... }, "current_menu": [ ... ] },
  "history":         [ { "role": "user", "content": "..." }, ... ],
  "trigzi_nickname": "Trigzi"
}
```

`system_context` may also include `spatial_context` (string) and `temporal_context` (string), which are folded into the prompt when present.

**SSE events:**

| Event | Payload | Notes |
|---|---|---|
| `text` | `{"content": "..."}` | Main response text (with the `Message:` prefix and any `[ACTION:...]` block stripped) |
| `action` | `{"tool": "...", "param": "..."}` | Optional UI command — split on first `\|` |
| `emoji` | `{"content": " 🥗"}` | Tone-matched emoji from the second-stage micro-inference |
| `done` | `{}` | Stream complete |
| `error` | `{"message": "..."}` | Stream failed |

| Status | Meaning |
|---|---|
| 200 SSE | Stream open |
| 400 | Invalid JSON, or missing `message` |

---

### `POST /api/v1/chat/onboarding`

Scripted onboarding assistant. Extracts the user's name, assigns a fallback nickname if none given, and fires a `set_name` action. Uses the same SSE pipeline as `/chat/stream` plus a `fact` event.

The route reuses the `chat_assistant` task config (model list, optimize, timeout) — there is no separate `onboarding` routing entry.

**Request:**
```json
{
  "message":         "Hi, I'm James",
  "fallback_name":   "Zesty Koala",
  "trigzi_nickname": "Trigzi"
}
```

**SSE events:**

| Event | Payload | Notes |
|---|---|---|
| `text` | `{"content": "..."}` | Welcome message |
| `fact` | `{"key": "user_name", "value": "James"}` | Extracted facts (`Fact: key=value` lines) |
| `action` | `{"tool": "set_name", "param": "James"}` | iOS side-effect |
| `emoji` | `{"content": " 👋"}` | Tone emoji |
| `done` | `{}` | Stream complete |

---

### `POST /api/v1/chat/sigmund`

High-EQ crisis de-escalation assistant. Drops all dietary behaviour and responds only with psychological support. No emoji flourish.

**Request:** Same shape as `/chat/stream`.

**SSE events:** `text`, `action` (crisis modal only), `done`, `error`.

> **Routing note:** This endpoint passes `optimize="accuracy"` literally to the router, **overriding** whatever is set under the `chat_sigmund` block in `llm_providers.json` (the `timeout` and `models` list are still read from config; only `optimize` is hardcoded). In the current router, `accuracy` mode means **A/B testing** — a random primary serves the user, with the others fired silently in the background for telemetry. It is **not** "always the highest-capability model." If you need deterministic primary-model selection on the crisis path, this is the place to fix it.

> **iOS gating:** there is no server-side detection of distress signals — this endpoint must be invoked directly by the iOS client when its on-device heuristics decide to escalate. (The `core/cognition/fml_analysis.py` Frontal Memory Lobe module exists but is not wired into any route.)

---

### `POST /api/v1/chat/emoji`

Isolated testing endpoint for the tone-evaluation micro-inference task. Not called by the iOS app directly — used for prompt iteration and benchmarking.

**Request:**
```json
{ "text": "This product contains gluten and is unsafe for coeliacs." }
```

**Response:**
```json
{ "status": "ok", "emoji": "⚠️" }
```

---

## Messages

### `GET /api/v1/messages`

Delivers one server-side message per device per day. Currently serves MOTD quotes. Selection is deterministic on `(date.toordinal(), md5(device_id))` — same device always receives the same message on a given day regardless of how many times it polls.

Dedup is **DB-backed** via `sessions.motd_last_date` (a `DATE` column, compared against `CURRENT_DATE`). This is consistent across all Hypercorn workers — no in-process state.

**Headers:**

| Header | Required | Description |
|---|---|---|
| `Authorization` | Yes | Must start with `Bearer ` (token contents not validated yet) |
| `X-Device-ID` | Yes | `UIDevice.current.identifierForVendor` UUID. Primary key in `sessions`. |
| `X-App-Version` | No | Stored on session for telemetry |

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `context` | string | Filter by message context. Pass `motd` for daily quotes. |
| `since` | int | Unix timestamp. **Reserved** for future server-push alerts; currently parsed but not used. |
| `force` | int | `1` skips deduplication. Testing only. |

**Response — new message:**
```json
[
  {
    "id":      "motd-013",
    "title":   "The Second Brain 🧠",
    "body":    "Your gut produces 95% of your serotonin. Breakfast is, quite literally, a mood decision.",
    "type":    "info",
    "context": "motd"
  }
]
```

**Response — already seen today:** `[]`

| Status | Meaning |
|---|---|
| 200 | Message array (0 or 1 items) |
| 400 | Missing `X-Device-ID` |
| 401 | `Authorization` header missing or doesn't start with `Bearer ` |

> See `core/messages/messages_service.py` for full delivery logic and how to add new sources.

---

## Telemetry

### `POST /api/v1/telemetry/unmatched`
### `POST /api/v1/telemetry/unmatched/gtin`
### `GET /api/v1/telemetry/unmatched/<gtin>`

Log an unmatched GTIN to the product acquisition queue (`logs/unmatched.log`). Called automatically by iOS when a barcode scan returns 404.

**POST body (JSON):**
```json
{ "term": "9310077217814" }
```

**GET path parameter:** `gtin` — the unmatched barcode.

**Response:**
```json
{ "status": "logged" }
```

All three routes are functionally equivalent — the iOS client uses `POST /api/v1/telemetry/unmatched/gtin`. Writes are non-blocking via `asyncio.to_thread`.

---

## Response conventions

| Convention | Detail |
|---|---|
| Content-Type | `application/json` unless SSE (`text/event-stream`) |
| SSE buffering | nginx `proxy_buffering off` required for all SSE routes |
| Error shape | `{"error": "<message>"}` with appropriate 4xx/5xx status |
| GTIN format | Always normalised to EAN-13 (13 digits, zero-padded) via `utils/gtin.normalise()` |
| Empty payloads | `{}` body returns 400 `{"error": "Invalid payload."}` |

---

## LLM routing summary

Per-task config lives in `core/llm/llm_providers.json` under `routing`. The `optimize` value selects the router mode (see README — `direct / failover / race / ab / cost`).

| Endpoint | `optimize` (effective) | Task config key | Notes |
|---|---|---|---|
| `GET /api/v1/product/<gtin>` | `failover` | `enrich` | Triggers enrichment pipeline if `enrichment_id` is null |
| `POST /api/v1/analyse/product` | `failover` | `analyse_product` | |
| `POST /api/v1/analyse/meal` | `failover` | `analyse_meal` | Vision-capable model required |
| `POST /api/v1/analyse/menu` | `failover` | `analyse_menu` | |
| `POST /api/v1/enrich/nutrition` | `failover` | `enrich_nutrition` | |
| `POST /api/v1/chat/stream` | `failover` (default) | `chat_assistant` | Falls back to `failover` if config absent |
| `POST /api/v1/chat/onboarding` | `failover` (default) | `chat_assistant` (reused) | |
| `POST /api/v1/chat/sigmund` | **`accuracy` (hardcoded — overrides config)** | `chat_sigmund` | Only `models` and `timeout` are read from config |
| `POST /api/v1/chat/emoji` | `speed` (default) | `chat_emoji` | 3s timeout |
| `GET /api/v1/messages` | — | — | No LLM. DB-backed dedup via `sessions.motd_last_date`. |
| Telemetry | — | — | No LLM. File I/O only. |

> "default" means the route falls back to that value if the task config is missing the `optimize` key. The router itself defaults to `failover` if no mode rule fires.
