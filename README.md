# Trigzi — Backend API

Dietary safety intelligence platform. Scans barcodes, looks up product data, enriches with clinical profiles (FODMAP, coeliac, histamine, allergens), and serves results to the iOS app via a Flask API.

**North star: less death.**

---

## Architecture

```
iOS App (scanner)
    │
    ▼
nginx (trigzi.com:443)
    │
    ▼
Gunicorn / Flask (127.0.0.1:5000)
    │
    ├── core/data_manager.py   — product lookup + LLM enrichment
    ├── core/telemetry.py      — scan logging + unmatched GTIN queue
    ├── core/db.py             — MariaDB connection pool
    └── core/llm/              — LLM abstraction layer (Gemini, Claude, OpenAI)
```

---

## Product Data Waterfall

```
A. MariaDB products table
   ├── enriched (clinical_profile set)  → return immediately
   └── unenriched                       → enrich via Gemini → return + queue for validation

B. Not found → 404 → iOS offers dual-camera capture
   └── OCR both images on device → POST to /api/v1/analyse/product → LLM analysis
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/product/<gtin>` | Product lookup. Returns JSON (enriched) or SSE stream (enriching) |
| `POST` | `/api/v1/analyse/product` | Unknown product analysis from OCR text |
| `POST` | `/api/v1/telemetry/unmatched/gtin` | Log unmatched GTIN for acquisition queue |

### SSE Response (unenriched product)

```
event: partial
data: {"status": "partial", "product": {...}}

event: enriched
data: {"status": "complete", "product": {...}}
```

---

## Database

MariaDB `trigzi` database on localhost.

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
    id          INT PRIMARY KEY,
    task        VARCHAR(50),    -- 'product', 'ocr_analysis'
    llm_model   VARCHAR(100),   -- 'gemini-2.5-flash'
    prompt_ver  VARCHAR(20),    -- 'extract_v2'
    prompt_hash CHAR(8),        -- SHA256[:8] of prompt text
    prompt_text MEDIUMTEXT      -- full prompt for exact reproduction
)
```

### Data Sources

| Source | Records | Notes |
|--------|---------|-------|
| Open Food Facts | ~2.2M | Global products, quality-filtered (no error tags) |
| Woolworths | ~48k | AU products, LLM-enriched |
| Coles | — | Pipeline ready |

### GTIN Normalisation

All GTINs normalised to EAN-13 via `utils/gtin.normalise()`:

- `< 13 digits` → zero-pad to 13
- `14 digits, leading 0` → strip leading digit → 13
- `14 digits, non-zero leader` → drop (EAN-14, non-consumer)
- `> 14 digits` → drop (invalid)

---

## Project Structure

```
app.py                  Flask application + routes
deploy.sh               Bounce gunicorn + nginx
run_tests.sh            Full test suite

core/
    data_manager.py     Product lookup, enrichment, OCR analysis
    db.py               MariaDB connection pool (DBUtils)
    telemetry.py        Scan logging + telemetry routes (Blueprint)
    llm/                LLM abstraction layer
        config.py       Provider config from llm_providers.json
        router.py       direct / race / failover / cost modes
        providers/      Gemini, Claude, OpenAI adapters
        probe.py        Live connectivity checker

utils/
    gtin.py             GTIN normalisation (OFF spec)
    off_lookup.py       MariaDB product lookup
    category_mapper.py  Woolworths/Coles → canonical category
    ingredient_parser.py Ingredient string tokeniser
    nutrition.py        Nutrition data normalisation
    tree.py             Clean project tree (excludes venv/__pycache__)

scripts/
    import_off_to_db.py     Import raw OFF JSONL → MariaDB
    import_enriched.py      Import enriched Woolworths/Coles JSONL → MariaDB
    normalise_enriched_gtins.py  Normalise GTINs in enriched JSONL
    off_profiler.py         Profile OFF JSONL schema (field coverage/types)
    llm_push.py             Stage 1: send scan file to LLM, save raw response
    llm_pull.py             Stage 2: parse LLM response → validate → schema
    probe_live.py           Live LLM provider connectivity check
    run_import.sh           nohup wrapper for import scripts

prompts/
    extract_v1.txt      Initial extraction prompt
    extract_v2.txt      + FODMAP reference table, serving/package fields

tests/
    test_gtin.py        GTIN normalisation (22 tests)
    test_errors.py      LLM error types
    test_config.py      LLM provider config
    test_filters.py     Request/response filters
    test_router.py      LLM routing modes
    test_probe.py       Provider probe

logs/
    import_off.log      OFF import progress
    scans/              Raw scan inputs (*_ocr.txt, *_enrich.txt)
    llm_responses/      Raw LLM responses for prompt iteration
    unmatched.log       Unmatched GTINs (product acquisition queue)
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

## Prompt Development

LLM extraction uses a two-stage pipeline decoupled for iteration:

```bash
# Stage 1: send scan file to LLM, save raw response
./scripts/llm_push.py logs/scans/1774500621_9310077217814_ocr.txt \
    --prompt prompts/extract_v2.txt

# Stage 2: parse response → validate → schema (repeat without hitting LLM)
./scripts/llm_pull.py logs/llm_responses/1774511242_9310077217814.txt
```

Scan files in `logs/scans/` are the regression test corpus.

---

## Operations

```bash
# Bounce services
./deploy.sh

# Run tests
./run_tests.sh

# Check LLM provider health
./scripts/probe_live.py all

# Product acquisition queue (sort by frequency)
sort logs/unmatched.log | uniq -c | sort -rn | head -50

# Import OFF data
./scripts/run_import.sh scripts/import_off_to_db.py \
    --input /data2000/openfoodfacts-products.jsonl --write

# Import enriched Woolworths/Coles
./scripts/run_import.sh scripts/import_enriched.py \
    --input /data2000/enriched_products_normalised.jsonl --write
```

---

## Environment

Variables in `/etc/trigzi/env` (loaded by systemd and scripts):

```bash
DB_HOST=localhost
DB_NAME=trigzi
DB_USER=trigzi
DB_PASS=...
GEMINI_API_KEY=...
```

---

## iOS App

The iOS companion app (`scanner`) is a SwiftUI application handling:

- Barcode scanning → GTIN lookup via this API
- SSE streaming for live enrichment updates
- Dual-camera capture for unknown products (front label + nutrition panel)
- On-device Vision OCR → POST to `/api/v1/analyse/product`
- `ProductNotFoundView` → `DualCaptureView` → `DiagnosticView` → `MealAnalysisView`
