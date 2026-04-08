# Trigzi Backend: Adding a New LLM Endpoint

This document outlines the strict architectural pipeline for creating a new LLM-powered endpoint in the Trigzi backend. 

To maintain blazing-fast response times (TTFT) and prevent JSON parsing crashes, Trigzi uses a **Flat-Text Extraction Protocol**. We do not ask the LLM for JSON directly; we ask for strictly formatted plaintext and parse it ourselves.

Follow these 7 steps in order when adding a new feature.

## 1. The Prompt Contract (`prompts/`)
Create a new `.txt` file in the `prompts/` directory. This file acts as a strict structural contract.

* **Rule 1:** Use bracketed headers (`[ACT AS]`, `[TASK]`, `[INSTRUCTIONS]`, `[OUTPUT]`).
* **Rule 2:** The `[OUTPUT]` block must end with exactly `---` on a new line.
* **Rule 3 (The "Lite" Model Rule):** If you are using fast/lite models (like Gemini Flash Lite), you **must** explicitly instruct the model to repeat the key names in the output and provide an example. Otherwise, it will just return the raw values.
* **Rule 4:** Variables are injected using Python's `.format()`. Use curly braces ONLY for variables (e.g., `{ocr_text}`).

**Example (`prompts/enrich_nutrition.txt`):**
```text
[ACT AS]
A clinical dietician and strict data extraction engine.

[TASK]
Extract nutritional values from the OCR text below.

[INSTRUCTIONS]
1. You MUST return the exact key name, followed by a colon, followed by the value.
2. Plaintext ONLY - DO NOT use JSON, markdown, or add explanations.
3. Example:
energy_kj: 1570

[OUTPUT]
energy_kj: <number>
protein_g: <number>
---

[OCR TEXT]
{ocr_text}
```

## 2. The Skills Library (`core/llm/skills.py`)
Wire the new prompt into the `SkillsLibrary` class so it can be loaded and formatted cleanly.

```python
@staticmethod
def enrich_nutrition_prompt(ocr_text: str) -> str:
    template = SkillsLibrary._load_prompt('enrich_nutrition.txt')
    return template.format(ocr_text=ocr_text)
```

## 3. The Analyser Engine (`core/analyser.py`)
Create the async function that calls the `router`, retrieves the raw string, and safely parses it into a typed Python dictionary.

* Wrap the parsing logic in `try/except` blocks.
* Map the parsed data strictly to the expected output schema (e.g., casting strings to `float` where necessary).

```python
async def enrich_nutrition(gtin: str, ocr_text: str) -> Optional[dict]:
    prompt = SkillsLibrary.enrich_nutrition_prompt(ocr_text)
    _cfg = llm_config.task_config("enrich_nutrition") 

    try:
        response = await router.analyze(
            payload       = {"prompt": prompt},
            profile       = "", 
            model_strings = _cfg["models"],
            optimize      = _cfg["optimize"],
            timeout       = _cfg["timeout"],
        )
        
        raw_text = str(response.get("result", "")).strip()
        # ... [Parse the flat text into a dict] ...
        return parsed_dict
        
    except Exception as e:
        logger.error(f"Task failed: {e}")
        return None
```

## 4. LLM Routing Configuration (`core/llm/llm_providers.json`)
The `LLMProviderConfig` is a thread-safe singleton that loads into RAM on boot. You **must** define your new task here so the router knows which models to use and how to optimize the call (e.g., `failover`, `race`, `cost`, `accuracy`).

Add your task to the `"routing"` block:
```json
"enrich_nutrition": {
  "models": [
    "claude-haiku-4-5-20251001",
    "gemini-2.5-flash-lite",
    "gpt-4.1-mini"
  ],
  "optimize": "failover",
  "timeout": 15
}
```
⚠️ **CRITICAL:** Because this is loaded into RAM, you MUST restart Gunicorn (`./deploy.sh`) after updating this file.

## 5. Benchmarking (`scripts/benchmark_task.py`)
Before deploying, benchmark the prompt against your selected models to ensure they respect the formatting contract and to determine the fastest model for the primary slot.

1.  Create a text fixture in `tests/fixtures/` (e.g., `enrich_nutrition_eval.txt`).
2.  Add your task to `get_task_payload_and_prompt()` in `scripts/benchmark_task.py`.
3.  Run the benchmark:
    `./scripts/benchmark_task.py enrich_nutrition -f tests/fixtures/enrich_nutrition_eval.txt`

## 6. The Flask Endpoint (`app.py`)
Create the route. Handle the incoming JSON, call your analyser function, optionally write to the database to seal data holes, and return the JSON.

```python
@app.route('/api/v1/enrich/nutrition', methods=['POST'])
async def enrich_nutrition_route():
    data = await request.get_json()
    # 1. Validate payload
    # 2. Call analyser: nutrition_data = await enrich_nutrition(gtin, text)
    # 3. Patch MariaDB (if applicable)
    return jsonify(nutrition_data), 200
```

## 7. API Contract Testing (`tests/schemas/endpoints/`)
We enforce strict typing between the backend and the iOS app. 

1.  Create a JSON Schema file for your new endpoint in `tests/schemas/endpoints/` (e.g., `enrich_nutrition.json`).
2.  Define the `test_payload` and the `response_schema`.
3.  Run the live-fire integration test against your local server to prove the endpoint honors the contract:
    `./scripts/validate_api_contracts.py`

