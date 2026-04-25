# Trigzi — Backend API

Dietary safety intelligence platform. Scans barcodes, looks up product data, enriches with clinical profiles (FODMAP, coeliac, histamine, allergens), analyses meals and menus, and serves results to the iOS app via an asynchronous Quart API.

**North star: less death.**

---

## Architecture

```text
iOS App (scanner)
    │
    ▼
nginx (trigzi.com:443)
    │
    ▼
Hypercorn / Quart ASGI (127.0.0.1:5000)
    │
    ├── core/data_manager.py   — read-only product lookup
    ├── core/enricher.py       — LLM enrichment + nutrition patching (writes)
    ├── core/analyser.py       — meal/menu/chat orchestration
    ├── core/personality.py    — persona instruction builder
    ├── core/sessions.py       — sessions table (MOTD dedup, token budget, tier)
    ├── core/telemetry.py      — scan/menu/unmatched logging + telemetry routes
    ├── core/messages/         — daily MOTD delivery service
    ├── core/cognition/        — FML executive layer (defined; not yet wired)
    ├── core/db.py             — aiomysql connection pool + enrichment registry
    └── core/llm/              — provider abstraction (Gemini, Claude, OpenAI)
```

The transport layer (`app.py`) is strictly HTTP/SSE handling. All DB reads go through `data_manager`, DB writes through `enricher`, and LLM orchestration through `analyser`.

---

## Product Data Waterfall

```text
A. MariaDB products table
   ├── enriched (clinical_profile set)  → return immediately (200 JSON)
   └── unenriched                       → enrich via Router → return + queue for validation (200 SSE)

B. Not found → 404 → iOS offers dual-camera capture
   └── OCR both images on device → POST to /api/v1/analyse/product → LLM analysis (200 / 422)
```

---

## API Endpoints

See `API_ENDPOINTS.md` for the full contract. Summary:

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/product/<gtin>` | Product lookup. Returns JSON (enriched) or SSE stream (enriching) |
| `POST` | `/api/v1/analyse/product` | Unknown product analysis from dual-camera OCR text |
| `POST` | `/api/v1/analyse/meal` | Plated-meal photo analysis against the user's dietary profile |
| `POST` | `/api/v1/analyse/menu` | Restaurant-menu OCR analysis |
| `POST` | `/api/v1/enrich/nutrition` | Patch missing nutrition data from an OCR'd panel |
| `POST` | `/api/v1/chat/stream` | Main dietary chat assistant (SSE) |
| `POST` | `/api/v1/chat/onboarding` | Scripted onboarding (SSE) |
| `POST` | `/api/v1/chat/sigmund` | High-EQ crisis de-escalation (SSE) |
| `POST` | `/api/v1/chat/emoji` | Tone-evaluation micro-inference (test/benchmark) |
| `GET`  | `/api/v1/messages` | One server-side message per device per day (MOTD) |
| `POST` | `/api/v1/telemetry/unmatched` (+ `/gtin`) | Log unmatched GTIN |
| `GET`  | `/api/v1/telemetry/unmatched/<gtin>` | Same, GET form |

### SSE response (unenriched product)

```text
event: progress
data: {"message": "Found Skim Milk by Black & Gold"}

event: progress
data: {"message": "Running latest analytics…"}

event: enriched
data: {"status": "complete", "product": { ... }}
```

On failure: `event: error` with `{"message": "..."}`.

---

## Database

MariaDB `trigzi` database on localhost. Three tables.

```sql
products (
    gtin          VARCHAR(13)  PRIMARY KEY,   -- normalised EAN-13
    source        VARCHAR(20),                -- 'off', 'woolworths', 'coles', 'ocr'
    name          VARCHAR(150),               -- denormalised for search
    enrichment_id INT → enrichments.id,       -- null = not yet enriched
    data          JSON,                       -- full unified product schema
    updated_at    TIMESTAMP
)

enrichments (
    id          INT PRIMARY KEY AUTO_INCREMENT,
    task        VARCHAR(50),    -- 'product', 'ocr_analysis'
    llm_model   VARCHAR(100),   -- 'gemini-2.5-flash'
    prompt_ver  VARCHAR(20),    -- 'enrich_v1'
    prompt_hash CHAR(8),        -- SHA256[:8] of prompt text — dedup key with llm_model
    prompt_text MEDIUMTEXT      -- full prompt for exact reproduction
)

sessions (
    device_id            VARCHAR(64) PRIMARY KEY,   -- iOS identifierForVendor UUID
    last_seen_at         TIMESTAMP,
    ip_last              VARCHAR(45),
    app_version          VARCHAR(32),
    motd_last_date       DATE,         -- last day a MOTD was delivered
    tier                 ENUM('free','paid'),
    tier_expires_at      DATETIME,
    tokens_used_today    INT,
    tokens_budget_daily  INT,          -- 50_000 free, 500_000 paid
    tokens_reset_date    DATE          -- lazy daily reset
)
```

Sessions ownership: `core/sessions.py` is the only writer. `core/messages/messages_service.py` reads via `motd_due()` / `record_motd_delivered()`. MOTD selection itself is deterministic on `(date.toordinal(), md5(device_id))` — the table only tracks "have we delivered today", never which message.

### Data sources

| Source | Records | Notes |
|--------|---------|-------|
| Open Food Facts | ~2.2M | Global products, quality-filtered (no error tags) |
| Woolworths | ~48k | AU products, LLM-enriched |
| Coles | — | Pipeline ready, not yet imported |
| BarcodeLookup | — | Fallback scraper, on-demand |

### GTIN normalisation

All GTINs normalised to EAN-13 via `utils/gtin.normalise()`:

- `< 13 digits` → zero-pad to 13 (covers EAN-8, UPC-A, UPC-E)
- `13 digits` → as-is (EAN-13 canonical)
- `14 digits, leading 0` → strip leading digit → 13
- `14 digits, non-zero leader` → drop (EAN-14, non-consumer)
- `> 14 digits` → drop (invalid)
- Non-digit input → drop

---

## Project Structure

```text
app.py                  Quart ASGI application + routes
deploy.sh               Bounce hypercorn + nginx
logs.sh                 Tail application logs
run_tests.sh            Full test suite

core/
    analyser.py         LLM orchestration (analyse_product/meal/menu, chat_*, enrich_nutrition)
    data_manager.py     Read-only product lookup (DEBUG_FORCE_NOT_FOUND / DEBUG_FORCE_UNENRICHED hooks)
    db.py               aiomysql connection pool + get_or_create_enrichment
    enricher.py         enrich() + patch_nutrition() — only writers besides sessions
    personality.py      get_persona_instruction() — Vibe × Audience matrix
    sessions.py         Sessions table service (upsert, motd_due, token budget, tier lapse)
    telemetry.py        Scan/menu/unmatched logging + telemetry Blueprint
    cognition/
        fml_analysis.py FrontalMemoryLobe — defined, NOT yet wired into routing
    messages/
        messages_service.py  Daily MOTD delivery
        motd.py             Quote catalogue (QUOTES list)
    llm/
        config.py        LLMProviderConfig singleton, llm_providers.json loader, task_config()
        router.py        5 modes: direct / failover / race / ab / cost
        validator.py     SchemaValidator: validate_prompt_contract, extract_blocks, validate_stream
        skills.py        SkillsLibrary — prompt loading + variable injection
        errors.py        LLMError class with @classmethod factories + is_failoverable
        probe.py         Provider health-check + ProbeScheduler (defined; not auto-started)
        filters/
            request_filter.py    Outbound payload shape per provider
            response_filter.py   Inbound text extraction per provider
            xml_filter.py        JSON ↔ XML for Claude scaffolding
        providers/
            base.py     BaseProvider + _perform_request payload dispatch
            claude.py   ClaudeProvider
            gemini.py   GeminiProvider
            openai.py   OpenAIProvider

utils/
    gtin.py             GTIN normalisation (OFF spec)
    off_lookup.py       MariaDB product lookup (OFFLookup class — get/save)
    category_mapper.py  Woolworths/Coles → canonical category
    ingredient_parser.py Ingredient string tokeniser
    nutrition.py        Nutrition data normalisation

providers/                 Note: top-level, not core/llm/providers/
    barcodelookup/
        barcodelookup.py   curl-cffi scraper, TLS impersonation
    woolworths/
        client.py
        formatter.py

prompts/                   See prompts/PROMPTS.md for the contract spec
    PROMPTS.md             Authoritative prompt-engineering rules
    analyse_food_image.txt /api/v1/analyse/meal
    analyse_menu.txt       /api/v1/analyse/menu
    analyse_text.txt       /api/v1/analyse/product (front + nutrition combined)
    chat_assistant.txt     /api/v1/chat/stream
    chat_emoji.txt         /api/v1/chat/emoji
    enrich_nutrition.txt   /api/v1/enrich/nutrition
    enrich_product.txt     Product enrichment (clinical profile)
    onboarding.txt         /api/v1/chat/onboarding
    sigmund_assistant.txt  /api/v1/chat/sigmund
    extract_v1.txt         Used by scripts/llm_push.py (offline batch only)
    extract_v2.txt         Used by scripts/llm_push.py — v1 + FODMAP table + serving fields

scripts/
    benchmark_task.py              Model evaluation runner
    generate_clean_frequencies.py  Word frequency builder for autocomplete dictionary
    import_off_to_db.py            OFF JSONL → MariaDB importer
    import_enriched.py             Woolworths/Coles enriched JSONL → MariaDB
    normalise_enriched_gtins.py    GTIN normalisation pass on enriched JSONL
    off_profiler.py                Schema profiler for the OFF dump
    llm_push.py                    Stage 1: send a scan to the router, write raw response
    llm_pull.py                    Stage 2: parse raw responses into structured JSON
    probe_client.py                Synthetic API client (reads tests/api_manifest.json)
    probe_live.py                  Provider health probe (gemini / claude / openai)
    tree.py                        Project tree printer
    validate_prompts.py            Prompt-contract validator (CI-friendly)

tests/
    test_analyser.py        Unit tests for analyse_*/chat_*/enrich_nutrition
    test_api_contracts.py   Live-server contract tests (reads tests/schemas/endpoints/*.json)
    test_enricher.py        Unit tests for enrich() + patch_nutrition() — see CAVEAT in CODE_REVIEW
    test_errors.py          LLMError factory tests
    test_filters.py         Request/response filter tests
    test_gtin.py            GTIN normalisation tests
    test_llm_providers.py   Provider config + resolution tests
    test_messages.py        MOTD service tests
    test_off_lookup.py      OFFLookup get/save tests
    test_personality.py     Persona matrix tests
    test_probe.py           Provider probe tests
    test_prompts.py         Prompt-file structural tests
    test_router.py          Router mode-resolution + execution tests
    api_manifest.json       Synthetic-client manifest (used by scripts/probe_client.py)
    fixtures/               Edge-case OCR/menu/nutrition test inputs
    schemas/endpoints/      Per-endpoint JSON schemas (used by test_api_contracts.py)

data/
    clean_words.txt         Autocomplete dictionary (used by ingredient parser)

eval/fixtures/              Real-world OCR captures for offline regression testing

logs/
    api.log                 /var/www/trigzi/logs/api.log — Quart application log
    import_off.log          OFF import progress
    scans/                  Raw scan inputs (*_ocr.txt, *_enrich.txt, *_menu_scan.txt)
    llm_responses/          Per-call dumps: <ts>_<STATUS>_<gtin>.txt
    unmatched.log           Unmatched GTINs (product acquisition queue)
    validate.jsonl          Enriched records pending human validation

docs/
    billing.md              Billing/tier policy notes

html/                       Static landing page assets

prompts/PROMPTS.md          Authoritative prompt-engineering specification
```

---

## Product Schema

```json
{
  "gtin": "0070177161170",
  "source": "off",
  "brand": "Twinings",
  "name": "Chai",
  "image_url": "https://images.openfoodfacts.org/...",
  "package_size": "20 bags",
  "category": "Tea Bags",
  "subcategory": "",
  "health_star_rating": 4.5,
  "serving_size_g": 1.5,
  "servings_per_pack": 20,
  "nutrition_100g": {
    "energy_kj": 145.0,
    "calories_kcal": 34.0,
    "protein_g": 3.2,
    "fat_total_g": 0.1,
    "fat_saturated_g": 0.1,
    "carbohydrates_g": 4.9,
    "sugars_g": 4.9,
    "fibre_g": null,
    "sodium_mg": 45.0
  },
  "raw_ingredients": "Black Tea, Vanilla Flavour...",
  "parsed_ingredients": ["black tea", "vanilla flavour"],
  "clinical_profile": {
    "estimated_health_star": 4.5,
    "fodmap_rating": 1,
    "coeliac_rating": 0,
    "histamine_rating": 2,
    "allergen_warnings": [],
    "health_summary": "Contains anti-inflammatory spices..."
  },
  "_source_id": "0070177161170",
  "_source_name": "off",
  "_enrichment_llm": "gemini-2.5-flash"
}
```

---

## LLM routing

`core/llm/router.py` exposes five execution modes, selected via `optimize` and the size of the model list:

| Mode | Triggered by | Behaviour |
|------|--------------|-----------|
| `direct` | Single model in list | One provider, one call. |
| `failover` | `optimize="failover"` (default) | Sequential — try each provider; failover on `is_failoverable` errors. |
| `race` | `optimize="speed"` | Fire all providers concurrently; return the fastest successful response, cancel the rest. |
| `ab` | `optimize="accuracy"` | Random primary returns to the user; secondaries fire silently in the background for telemetry. **Note: this is A/B testing — it does NOT guarantee the highest-capability model.** |
| `cost` | `optimize="cost"` | Pick the cheapest model (per output-token rate) across all providers. |

Per-task routing is configured in `core/llm/llm_providers.json` under the `routing` block. See `NEW_ENDPOINTS.md` step 4.

---

## Operations

```bash
# Bounce services
./deploy.sh

# Tail application logs
./logs.sh
journalctl -u trigzi_api -n 50 -f

# Synthetic client probe against the local server
./scripts/probe_client.py

# LLM provider health
./scripts/probe_live.py all
./scripts/probe_live.py gemini claude

# Product acquisition queue (sort by frequency)
sort logs/unmatched.log | uniq -c | sort -rn | head -50

# Import OFF data
./scripts/run_import.sh scripts/import_off_to_db.py \
    --input /data2000/openfoodfacts-products.jsonl --write
```

---

## Environment

Variables in `/etc/trigzi/env` (loaded by systemd override and the `trigzi` shell function):

```bash
DB_HOST=localhost
DB_PORT=3306
DB_NAME=trigzi
DB_USER=trigzi
DB_PASS=...
GEMINI_API_KEY=...
CLAUDE_API_KEY=...
OPENAI_API_KEY=...
```

API key env-var names are exact — see `core/llm/providers/{gemini,claude,openai}.py`. `CLAUDE_API_KEY` (not `ANTHROPIC_API_KEY`) and `GEMINI_API_KEY` (not `GOOGLE_API_KEY`).

### API keys & environment architecture (the dual-state trap)

Trigzi centralises all secrets and configuration (DB credentials, LLM API keys) in `/etc/trigzi/env`. Because this file uses standard bash `export VAR=value` syntax so it can be sourced from any developer shell, two distinct mechanisms consume it:

**1. Command line / scripts (interactive)**
Maintenance scripts (e.g. `./scripts/probe_live.py`) need the keys in the active shell. The `trigzi()` shell function in `/root/.bashrc` sources `/etc/trigzi/env`, activates the venv, and `cd`s into the project. See `SETUP.md` §4 for the canonical definition.

**2. The live application (systemd)**
Quart loads its environment via a systemd drop-in override at `/etc/systemd/system/trigzi_api.service.d/override.conf`. systemd v246+ tolerates the `export` prefix on each line of an `EnvironmentFile`, which is why the same file works for both the shell function and systemd. RHEL/Rocky 9 ships systemd 252; RHEL 8 may need the `export` keywords stripped from the systemd-consumed copy.

⚠️ **Applying environment changes:** updating a key in `/etc/trigzi/env` does not affect the running app until the master process is recycled. Run `./deploy.sh`, which performs `systemctl restart trigzi_api`.

---

## iOS app

The iOS companion app (`scanner`) is a SwiftUI application handling:

- Barcode scanning → GTIN lookup via this API
- SSE streaming for live enrichment updates
- Dual-camera capture for unknown products (front label + nutrition panel)
- On-device Vision OCR → POST to `/api/v1/analyse/product`
- `ProductNotFoundView` → `DualCaptureView` → `DiagnosticView` → `MealAnalysisView`
- Daily MOTD polling against `GET /api/v1/messages` with `X-Device-ID`
