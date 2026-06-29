#!/usr/bin/env python3
"""
=============================================================================
Module:        Allergen Corpus Analyser
Location:      scripts/analyse_corpus.py
Description:   Runs a FlashText (Aho-Corasick) scan over a raw ingredient
               corpus dump to measure the real-world hit rate and false-
               positive exposure of the EU allergen tripwire dictionary.

               Outputs:
                 - Console: ranked per-allergen hit counts and top triggers
                 - false_positive_audit_log.txt: first 50 match contexts per
                   trigger word for manual review

               Typical corpus: /var/lib/mysql-files/off_ingredients_dump.txt
               (Open Food Facts ingredients, one product per line)

Usage:
    python scripts/analyse_corpus.py
=============================================================================
"""

import json
from collections import defaultdict
from flashtext import KeywordProcessor


def load_tripwires_flashtext(json_path: str) -> KeywordProcessor:
    """Build a FlashText keyword processor from the EU allergen JSON dictionary.

    Each allergen variant is loaded into an Aho-Corasick automaton keyed as
    'allergy::variant' so a single match returns both the allergen category
    and the exact surface form that triggered it — no second lookup needed.

    Args:
        json_path: Path to the multilingual allergen JSON file.

    Returns:
        A configured KeywordProcessor ready for corpus scanning.
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    kp = KeywordProcessor(case_sensitive=False)
    seen_variants: set[str] = set()

    for _lang, allergies in data.items():
        for allergy, variants in allergies.items():
            for variant in variants:
                clean_variant = variant.lower()
                if clean_variant not in seen_variants:
                    kp.add_keyword(clean_variant, f"{allergy}::{clean_variant}")
                    seen_variants.add(clean_variant)

    print(f"✅ Trie Built: Loaded {len(seen_variants)} unique trigger words.")
    return kp


def run_corpus_analysis(txt_path: str, kp: KeywordProcessor) -> None:
    """Scan every ingredient line in txt_path and print a ranked impact report.

    Writes false_positive_audit_log.txt alongside the console output — the
    first 50 match contexts per trigger word for manual false-positive review.

    Args:
        txt_path: Path to the plain-text corpus (one product per line).
        kp:       Configured FlashText KeywordProcessor from load_tripwires_flashtext.
    """
    print("🚀 Starting FlashText Corpus Analysis...")

    hit_counts: dict[str, int] = defaultdict(int)
    trigger_frequencies: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    with open('false_positive_audit_log.txt', 'w', encoding='utf-8') as audit_log:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for row_idx, line in enumerate(f):
                ingredients = line.strip()
                if not ingredients:
                    continue

                if row_idx % 100_000 == 0:
                    print(f"Processed {row_idx:,} records...")

                # span_info=True forces FlashText to return start/end indices
                # so we can extract surrounding context for the audit log.
                matches = kp.extract_keywords(ingredients, span_info=True)

                for match in matches:
                    meta, start, end = match
                    allergy, matched_text = meta.split("::")

                    hit_counts[allergy] += 1
                    trigger_frequencies[allergy][matched_text] += 1

                    if trigger_frequencies[allergy][matched_text] <= 50:
                        ctx_start = max(0, start - 40)
                        ctx_end   = min(len(ingredients), end + 40)
                        context   = ingredients[ctx_start:ctx_end]
                        audit_log.write(
                            f"[{allergy.upper()}] Trigger: '{matched_text}' "
                            f"| Context: ...{context}...\n"
                        )

    print("\n" + "=" * 50)
    print("📊 CORPUS IMPACT REPORT (OFF Records)")
    print("=" * 50)

    for allergy, count in sorted(hit_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"\n🚨 {allergy.upper()}: {count:,} total hits")
        top_triggers = sorted(
            trigger_frequencies[allergy].items(),
            key=lambda x: x[1],
            reverse=True,
        )[:50]
        for word, freq in top_triggers:
            print(f"   -> '{word}': {freq:,} times")


if __name__ == "__main__":
    keyword_processor = load_tripwires_flashtext('tests/fixtures/EU_allergens_multilingual.json')
    run_corpus_analysis('/var/lib/mysql-files/off_ingredients_dump.txt', keyword_processor)
