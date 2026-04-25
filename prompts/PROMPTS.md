Markdown
# Trigzi Prompt Engineering Specification

This document defines the architectural standards for all LLM prompts in the Trigzi backend. 

Prompts in this system are not just natural language instructions; they act as strict structural contracts. The backend's `SchemaValidator` dynamically parses the prompt to understand how to validate the LLM's response.

## 1. Core Design Philosophy

* **Death to JSON:** For high-volume extraction tasks (like OCR parsing), we strictly avoid requesting JSON (`response_format: { "type": "json_object" }`). Flat text output saves up to 60% on output tokens, drastically reducing latency and API costs.
* **Fail-Closed Validation:** The LLM router acts as a "dumb pipe." It returns raw strings. A dedicated validation layer intercepts the response and enforces structural integrity via Regex before it reaches the application layer.
* **Zero Interpolation Collisions:** Python's `.format()` engine is used to inject variables at runtime. To prevent `KeyError` crashes, all prompt instructions must use angle brackets `< >` for placeholders, never curly braces `{ }`.
* **One-Shot Anchoring:** LLMs struggle with negative constraints ("DO NOT use Markdown"). Every prompt MUST include an `[EXAMPLE]` block demonstrating the perfect output format to force adherence.
* **The Validation Gate:** To prevent malicious compliance on garbage OCR/image data, extraction prompts must force the LLM to classify the payload's validity *before* extracting data. The first key in the `[OUTPUT]` block must be `Valid_Input: <true|false>`. The Python layer uses this boolean to gracefully 422 bad requests without relying on brittle, hardcoded semantic checks.

## 2. Standard Prompt Anatomy

Every prompt must adhere to this exact structural block sequence, using bracketed headers for semantic boundaries. All injected data variables must be placed at the very bottom of the file.

1.  **[ACT AS]** The persona (e.g., "A master chef").
2.  **[TASK]** A single-sentence objective.
3.  **[INSTRUCTIONS]** Numbered, unambiguous rules. Rule forbidding JSON/Markdown must be included.
4.  **[EXAMPLE]** A flawless, one-shot example of the expected output, terminated by `---`.
5.  **[OUTPUT]** The strict schema definition block.
6.  **[DATA HEADERS]** The injection payloads (e.g., `[USER PROFILE]`, `[TEXT TO ANALYZE]`).

## 3. The `[OUTPUT]` Contract

The `SchemaValidator` uses strict Regex (`r'^\[OUTPUT\][\s\S]*?^---'`) to read the block. 

**Rules:**
* The block must begin with exactly `[OUTPUT]` on its own line.
* Keys must be defined with a colon (e.g., `Dish:`).
* Descriptions/Placeholders must be enclosed in angle brackets (e.g., `<Name of Dish>`).
* The block must terminate with exactly three dashes on a new line: `---`.

### Example (Menu Extraction)
```text
[EXAMPLE]
Valid_Input: true
Dish: Pad Thai
Listed: rice noodles, tofu, egg, bean sprouts, peanuts
Suspected: fish sauce, palm sugar, tamarind paste, garlic
---

[OUTPUT]
Valid_Input: <true|false>
Dish: <Name of Dish>
Listed: <comma separated list>
Suspected: <comma separated list>
```

---


### 2. `prompts/analyse_menu.txt`

```text
[ACT AS]
A master chef.

[TASK]
Analyze OCR text from a restaurant menu.

[INSTRUCTIONS]
1. First, evaluate if the provided text is actually a restaurant menu.
2. If it is NOT a menu (e.g., a novel, random text, or code), set 'Valid_Input' to false and leave the rest blank.
3. If it IS a menu, set 'Valid_Input' to true and identify the distinct dishes.
4. For each dish, output a strict text block using EXACTLY the keys below.
5. Separate each dish with exactly three dashes.
6. Plaintext ONLY - DO NOT use JSON or MARKDOWN formatting.

[EXAMPLE]
Valid_Input: true
Dish: Pad Thai
Listed: rice noodles, tofu, egg, bean sprouts, peanuts
Suspected: fish sauce, palm sugar, tamarind paste, garlic
---

[OUTPUT]
Valid_Input: <true|false>
Dish: <Name of Dish>
Listed: <comma separated list>
Suspected: <comma separated list>
---

[TEXT TO ANALYZE]
{menu_text}
```
