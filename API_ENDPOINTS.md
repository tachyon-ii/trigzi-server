# Trigzi Backend — API Endpoints

Base URL: `https://trigzi.com`  
All endpoints require `Authorization: Bearer <token>` unless noted.

---

## Product

### `GET /api/v1/product/<gtin>`

Look up a product by barcode. Returns immediately if already enriched. Streams SSE if enrichment is needed.

**Path parameter:** `gtin` — 8–14 digit barcode string.

**Response — enriched (JSON):**
```json
{ "status": "complete", "product": { ... } }
```

**Response — unenriched (SSE stream):**
```
event: progress
data: {"message": "Found Skim Milk by Black & Gold"}

event: progress
data: {"message": "Running latest analytics…"}

event: enriched
data: {"status": "complete", "product": { ... }}
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
| 400 | Invalid barcode format |

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
{ "status": "ok", "result": { ... } }
```

| Status | Meaning |
|---|---|
| 200 | Analysis complete |
| 400 | Missing or invalid payload |
| 500 | LLM analysis failed |

---

### `POST /api/v1/enrich/nutrition`

Extract missing nutrition data from an OCR'd nutrition panel and patch the global database. Called when a product exists but has null `nutrition_100g`.

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

| Status | Meaning |
|---|---|
| 200 | Nutrition data extracted; database patched |
| 400 | Missing `gtin` or `ocr_text` |
| 500 | LLM extraction failed |

---

## Meal & Menu Analysis

### `POST /api/v1/analyse/meal`

Analyse a photo of a plated meal against the user's dietary profile. Accepts base64-encoded JPEG.

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
    "type": "meal_photo",
    "items": [
      {
        "name": "Pad Thai",
        "verdict": "Caution",
        "summary": "Contains fish sauce and peanuts.",
        "warnings": ["fish", "peanuts"],
        "ingredients": ["noodles", "egg", "fish sauce", "peanuts"],
        "flaggedIngredients": ["fish sauce", "peanuts"],
        "detailedReason": "..."
      }
    ]
  }
}
```

| Status | Meaning |
|---|---|
| 200 | Analysis complete |
| 400 | Missing `image` field |
| 500 | LLM analysis failed |

---

### `POST /api/v1/analyse/menu`

Analyse OCR-extracted restaurant menu text. Returns dish names with listed and suspected ingredients. Raw OCR scan is saved to `logs/scans/` for regression testing.

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
| 500 | LLM analysis failed |

---

## Chat

### `POST /api/v1/chat/stream`

Main dietary chat assistant. Streams SSE. Runs a two-stage pipeline: (1) clinical response + optional UI action command, (2) emoji micro-inference appended as a separate event.

**Request:**
```json
{
  "message":         "Is the beetroot salad safe for me?",
  "system_context":  { "dietary_profile": { ... }, "current_menu": [ ... ] },
  "history":         [ { "role": "user", "content": "..." }, ... ],
  "trigzi_nickname": "Trigzi"
}
```

**SSE events:**

| Event | Payload | Notes |
|---|---|---|
| `text` | `{"content": "..."}` | Main response text |
| `action` | `{"tool": "...", "param": "..."}` | Optional UI command (e.g. `trigger_safety_modal`) |
| `emoji` | `{"content": " 🥗"}` | Tone-matched emoji, appended after text |
| `done` | `{}` | Stream complete |
| `error` | `{"message": "..."}` | Stream failed |

| Status | Meaning |
|---|---|
| 200 SSE | Stream open |
| 400 | Missing `message` |

---

### `POST /api/v1/chat/onboarding`

Scripted onboarding assistant. Extracts the user's name, assigns a fallback nickname if none given, and fires a `set_name` action. Uses the same SSE pipeline as `/chat/stream` with an additional `fact` event type.

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
| `fact` | `{"key": "user_name", "value": "James"}` | Extracted facts |
| `action` | `{"tool": "set_name", "param": "James"}` | iOS side-effect |
| `emoji` | `{"content": " 👋"}` | Tone emoji |
| `done` | `{}` | Stream complete |

---

### `POST /api/v1/chat/sigmund`

High-EQ crisis de-escalation assistant. Activated when the FML layer detects distress signals. Drops all dietary behaviour — responds only with psychological support. Forces `accuracy` routing (highest-capability model). No emoji flourish.

**Request:** Same shape as `/chat/stream`.

**SSE events:** `text`, `action` (crisis modal only), `done`, `error`.

> If the user expresses self-harm intent, this endpoint appends `[ACTION: trigger_safety_modal|severe_crisis]` which the iOS client intercepts to surface emergency resources.

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

Delivers one server-side message per device per day. Currently serves MOTD quotes. Selection is deterministic on `(date, device_id)` — same device always receives the same message on a given day regardless of how many times it polls.

**Headers:**

| Header | Required | Description |
|---|---|---|
| `X-Device-ID` | Yes | `UIDevice.current.identifierForVendor` UUID. Deduplication key. |

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `context` | string | Filter by message context. Pass `motd` for daily quotes. |
| `since` | int | Unix timestamp. Reserved for future server-push alerts. |
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
| 401 | Missing or malformed `Authorization` |

> See `docs/messages_endpoint.md` for full delivery logic and extension guide.

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

All three routes are functionally equivalent — the iOS client uses `POST /api/v1/telemetry/unmatched/gtin`.

---

## Response conventions

| Convention | Detail |
|---|---|
| Content-Type | `application/json` unless SSE (`text/event-stream`) |
| SSE buffering | nginx `proxy_buffering off` required for all SSE routes |
| Error shape | `{"error": "<message>"}` with appropriate 4xx/5xx status |
| Auth | `Authorization: Bearer <token>` on all endpoints |
| GTIN format | Always normalised to EAN-13 (13 digits, zero-padded) via `utils/gtin.normalise()` |

---

## LLM routing summary

| Endpoint | Optimize | Notes |
|---|---|---|
| `GET /api/v1/product/<gtin>` | `failover` | Enrichment — `enrich` task config |
| `POST /api/v1/analyse/product` | `failover` | `analyse_product` task config |
| `POST /api/v1/analyse/meal` | `failover` | `analyse_meal` task config |
| `POST /api/v1/analyse/menu` | `failover` | `analyse_menu` task config |
| `POST /api/v1/enrich/nutrition` | `failover` | `enrich_nutrition` task config |
| `POST /api/v1/chat/stream` | `failover` | `chat_assistant` task config |
| `POST /api/v1/chat/onboarding` | `failover` | Reuses `chat_assistant` config |
| `POST /api/v1/chat/sigmund` | `accuracy` (forced) | `chat_sigmund` config — always highest-capability model |
| `POST /api/v1/chat/emoji` | `speed` | `chat_emoji` config — micro-inference, 3s timeout |
| `GET /api/v1/messages` | — | No LLM. Static data with in-process dedup. |
| Telemetry | — | No LLM. File I/O only. |
