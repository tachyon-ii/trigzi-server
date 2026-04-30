# Clinical Failsafe Tripwire Architecture

## Overview
The **Clinical Failsafe Scanner** acts as a deterministic, multi-lingual "dead-man's switch" for dietary threat evaluation. In a health-tech context, clinical safety cannot rely entirely on probabilistic LLM outputs due to the risk of hallucinations or misinterpretation of ambiguous OCR text. The tripwire ensures that if a known biological threat token is detected in the raw input, the system forces a hard "Avoid" state, circumventing the LLM entirely.

## The Aho-Corasick / Word Boundary Mechanism
To evaluate text with high performance and zero substring collision, the engine relies on strict word boundaries (`\b`). 
- **Swift Implementation:** Utilizes native Apple Regex engines, compiling the multilingual dictionary into a highly vectorized, memory-aligned DFA.
- **Data Validation Implementation:** Uses the Aho-Corasick automaton (via FlashText in Python) to achieve $O(N)$ scanning time, independent of the dictionary size.

This guarantees that a token like `pea` will not trigger `peanut`, and `weight` will not trigger `ei` (German for egg).

## Empirical Corpus Validation
To tune the signal-to-noise ratio and prevent alert fatigue, the multi-lingual JSON dictionary was run against a corpus of **1.1 million products** from the Open Food Facts (OFF) database, representing roughly 5-10 million distinct ingredient tokens.

### Key Findings & Surgical Pruning
Theoretical edge cases (e.g., `cod` matching Cape Cod, or `bass` matching Bass Ale) registered as statistical anomalies (e.g., < 0.009% occurrence rate). The UI is designed to handle these rare collisions transparently (e.g., *"Alert: 'bass' is English for fish"*).

However, the empirical test exposed two catastrophic cross-lingual collisions that required surgical pruning from the source JSON:
1. **`ou` (Romanian: Egg):** Collided with the French and Portuguese conjunction for *"or"* (e.g., "sucre ou sirop").
2. **`polvo` (Portuguese: Octopus/Mollusc):** Collided with the Spanish and Portuguese word for *"powder"* (e.g., "leche en polvo").

These entries were deleted to preserve the Positive Predictive Value (PPV) without compromising the Negative Predictive Value (NPV).

## Architectural Principle: Rejecting NLP Gating
A proposed architecture to gate the tripwire using an NLP Language Detector (e.g., Apple's `NLLanguageRecognizer`) was strictly rejected due to the **Loan Word Vulnerability**.
- **The Problem:** Food menus are inherently multi-lingual. A menu written in English syntax ("Loaded Nachos with Queso") will be classified as English. If the language gate requires a match, the Spanish token `queso` (Dairy) would be downgraded or ignored.
- **The Rule:** A probabilistic AI guess must *never* override a deterministic safety lock. We let the tripwire fire globally and allow the human user to disambiguate the UI alert, rather than risking a silent failure (failing open).

## Defense-in-Depth: Fuel Starvation (`isVerified`)
To ensure the mathematical engine never "fails open" on a typo or novel chemical (e.g., `quantum_crystalline_sugar`), the `IngredientManager` flags tokens that completely miss the L1/L2 dictionary caches with `isVerified = false`. The Dietary Analysis Engine cannot mathematically evaluate an unverified ingredient as "Safe," forcing the UI to display an "Unknown Ingredient" warning.

## Server Infrastructure & Testing Scripts
The validation test scripts and data reside on the server at the following paths:

* **Test Script:** `scripts/analyse_corpus.py` (FlashText implementation)
* **Dictionary Fixture:** `tests/fixtures/EU_allergens_multilingual.json`
* **Corpus Dump:** `/var/lib/mysql-files/off_ingredients_dump.txt`

### Running the Corpus Analysis
```bash
python scripts/analyse_corpus.py
