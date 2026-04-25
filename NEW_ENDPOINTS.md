# Trigzi Backend: Adding a New LLM Endpoint

This document outlines the strict architectural pipeline for creating a new LLM-powered endpoint in the Trigzi backend.

To maintain blazing-fast response times (TTFT) and prevent JSON parsing crashes, Trigzi uses a **Flat-Text Extraction Protocol**. We do not ask the LLM for JSON directly; we ask for strictly formatted plaintext and parse it ourselves with `SchemaValidator.extract_blocks` against a list of expected keys.

Follow these 7 steps in order when adding a new feature.

## 1. The Prompt Contract (`prompts/`)
Create a new `.txt` file in the `prompts/` directory. This file is a strict structural contract — see `prompts/PROMPTS.md` for the authoritative specification.

* **Rule 1:** Use bracketed headers (`[ACT AS]`, `[TASK]`, `[INSTRUCTIONS]`, `[OUTPUT]`).
* **Rule 2:** The `[OUTPUT]` block must end with exactly `---` on a new line.
* **Rule 3 (The Semantic Gate):** The first key in the `[OUTPUT]` block **MUST** be `Valid_Input: <true|false>`. Instruct the LLM to classify the incoming data's validity *before* extracting. This prevents malicious compliance and hallucinations on garbage data.
* **Rule 4:** Variables are injected using Python's `.format()`. Use curly braces ONLY for variables (e.g. `{ocr_text}`). All non-variable angle-bracket placeholders inside the prompt body must use `< >`, never `{ }`, to avoid `KeyError`.

**Example (`prompts/enrich_nutrition.txt`):**
```text
[ACT AS]
A clinical dietician and strict data extraction engine.

[TASK]
Extract nutritional values from the OCR text below.

[INSTRUCTIONS]
1. First, evaluate if the text is a valid nutrition panel. If not, set 'Valid_Input' to false and leave the rest blank.
2. If valid, set 'Valid_Input' to true.
3. You MUST return the exact key name, followed by a colon, followed by the value.
4. Plaintext ONLY - DO NOT use JSON.

[EXAMPLE]
Valid_Input: true
energy_kj: 1570
---

[OUTPUT]
Valid_Input: <true|false>
energy_kj: <number>
---

[OCR TEXT]
{ocr_text}
```

## 2. The Skills Library (`core/llm/skills.py`)
Wire the new prompt into `SkillsLibrary` so it can be loaded and formatted cleanly.

```python
@staticmethod
def enrich_nutrition_prompt(ocr_text: str) -> str:
    template = SkillsLibrary._load_prompt('enrich_nutrition.txt')
    return template.format(ocr_text=ocr_text)
```

## 3. The Analyser Engine (`core/analyser.py`)
Create the async function that calls the router and parses the response. **You must pass `expected_keys`** — without it, the router returns the raw flat-text string in `result` and `parsed_blocks` will be empty.

* Wrap the router call in `try/except`.
* Check the `Valid_Input` boolean to gracefully reject bad inputs (triggers a 422 downstream).
* Strip `valid_input` from the dict before returning to the transport layer.

```python
async def enrich_nutrition(gtin: str, ocr_text: str) -> Optional[dict]:
    prompt = SkillsLibrary.enrich_nutrition_prompt(ocr_text)
    _cfg = llm_config.task_config("enrich_nutrition")

    expected_keys = ["valid_input", "energy_kj", "calories_kcal",
                     "protein_g", "fat_total_g", "fat_saturated_g",
                     "carbohydrates_g", "sugars_g", "fibre_g", "sodium_mg"]

    try:
        response = await router.analyse(
            payload       = {"prompt": prompt},
            profile       = "",
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
            expected_keys = expected_keys,        # <-- REQUIRED
        )

        # Path A: pull from parsed_blocks (cleanest)
        blocks = response.get("parsed_blocks") or []
        if not blocks:
            return None

        # Path B (equivalent): re-parse manually
        # raw = str(response.get("result", ""))
        # blocks = SchemaValidator.extract_blocks(raw, expected_keys)

        # The Semantic Gate Check
        if str(blocks[0].get("valid_input", "")).lower() == "false":
            logger.warning("LLM flagged data as invalid.")
            return None

        blocks[0].pop("valid_input", None)
        return blocks[0]

    except Exception as e:
        logger.error(f"Task failed: {e}")
        return None
```

> **Key naming gotcha:** `extract_blocks` lowercases each key. A prompt that emits `Energy KJ:` becomes the dict key `energy kj` (with a space). If you want snake_case keys in the resulting dict, use snake_case in the prompt's `[OUTPUT]` block too — `energy_kj:` is correct.

## 4. LLM Routing Configuration (`core/llm/llm_providers.json`)
`LLMProviderConfig` is a thread-safe singleton that loads `llm_providers.json` once at startup. Define your new task in the `"routing"` block so the router knows which models to use and how to optimize.

```json
"enrich_nutrition": {
  "models": [
    "claude-haiku-4-5-20251001",
    "gemini-2.5-flash",
    "gpt-4.1-mini"
  ],
  "optimize": "failover",
  "timeout": 15
}
```

| Field | Purpose | Default if absent |
|---|---|---|
| `models` | Provider list (or full model strings); router resolves and falls over in order | `["gemini","claude","openai"]` |
| `optimize` | Router mode selector: `failover` / `speed` / `accuracy` / `cost` | `failover` |
| `timeout` | Per-call timeout in seconds | `60.0` |

⚠️ **Because this is loaded into RAM, you MUST bounce Hypercorn (`./deploy.sh`) after updating this file.**

## 5. Benchmarking (`scripts/benchmark_task.py`)
Before deploying, benchmark the prompt against your selected models to ensure they respect the formatting contract.

1. Create a text fixture in `tests/fixtures/` (e.g. `enrich_nutrition_eval.txt`).
2. Add your task to `get_task_payload_and_prompt()` in `scripts/benchmark_task.py`.
3. Run the benchmark:
   ```
   ./scripts/benchmark_task.py enrich_nutrition -f tests/fixtures/enrich_nutrition_eval.txt
   ```

## 6. The Quart Endpoint (`app.py`)
Create the async Quart route. Handle the incoming JSON, call your analyser function, return 422 on the rejection state, and 200 with the payload otherwise.

```python
@app.route('/api/v1/enrich/nutrition', methods=['POST'])
async def enrich_nutrition_route():
    data = await request.get_json()
    if not data:
        return jsonify({"error": "Invalid payload."}), 400

    gtin     = data.get('gtin', '').strip()
    ocr_text = data.get('ocr_text', '').strip()

    if not gtin or not ocr_text:
        return jsonify({"error": "Missing gtin or ocr_text."}), 400

    nutrition_data = await enrich_nutrition(gtin, ocr_text)
    if not nutrition_data:
        return jsonify({"error": "Unprocessable OCR"}), 422

    # Optionally patch MariaDB
    await patch_nutrition(gtin, nutrition_data)

    return jsonify(nutrition_data), 200
```

## 7. API Contract Testing
Trigzi has **two parallel contract systems**, both of which should be updated:

### a. `tests/api_manifest.json` — used by `scripts/probe_client.py`
A single manifest file consumed by the synthetic-client probe. Add an entry under `endpoints` with:
- `name`, `method`, `route`
- A **valid** test case (200 path, response-shape assertion)
- An **invalid** test case (400 path) using a malformed payload
- An **unprocessable** test case (422 path) using garbage data — proves the `Valid_Input` semantic gate is firing

Run against your local server:
```
./scripts/probe_client.py
```

### b. `tests/schemas/endpoints/<your_endpoint>.json` — used by `tests/test_api_contracts.py`
A per-endpoint JSON Schema file consumed by the live-server pytest contract harness. Each file declares `path`, `test_payload`, and a JSON Schema (`response_schema` for REST, `sse_schemas` for streaming routes).

Run with the server up:
```
python tests/test_api_contracts.py
```

Both should pass before merging. The `probe_client.py` path catches behavioural regressions; the `test_api_contracts.py` path catches schema drift.
