#!/usr/bin/env python3
import json
from collections import defaultdict
from flashtext import KeywordProcessor

def load_tripwires_flashtext(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Initialize the Aho-Corasick automaton
    # case_sensitive=False natively handles our lowercase requirements
    kp = KeywordProcessor(case_sensitive=False)
    
    # Track variants to avoid duplicate processing
    seen_variants = set()
    
    for lang, allergies in data.items():
        for allergy, variants in allergies.items():
            for variant in variants:
                clean_variant = variant.lower()
                if clean_variant not in seen_variants:
                    # We map the keyword to a combined string so when FlashText 
                    # finds a match, it hands us back BOTH the allergy category 
                    # and the exact word that triggered it.
                    kp.add_keyword(clean_variant, f"{allergy}::{clean_variant}")
                    seen_variants.add(clean_variant)
                    
    print(f"✅ Trie Built: Loaded {len(seen_variants)} unique trigger words.")
    return kp

def run_corpus_analysis(txt_path, kp):
    print("🚀 Starting FlashText Corpus Analysis...")
    
    hit_counts = defaultdict(int)
    trigger_frequencies = defaultdict(lambda: defaultdict(int))
    
    with open('false_positive_audit_log.txt', 'w', encoding='utf-8') as audit_log:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for row_idx, line in enumerate(f):
                ingredients = line.strip()
                if not ingredients: continue
                
                if row_idx % 100000 == 0:
                    print(f"Processed {row_idx:,} records...")
                
                # span_info=True forces FlashText to return the start/end indices 
                # so we can still extract the surrounding text for the audit log.
                matches = kp.extract_keywords(ingredients, span_info=True)
                
                for match in matches:
                    # FlashText returns: ('allergy::variant', start_idx, end_idx)
                    meta, start, end = match
                    allergy, matched_text = meta.split("::")
                    
                    hit_counts[allergy] += 1
                    trigger_frequencies[allergy][matched_text] += 1
                    
                    # Log the first 50 occurrences for manual context auditing
                    if trigger_frequencies[allergy][matched_text] <= 50:
                        ctx_start = max(0, start - 40)
                        ctx_end = min(len(ingredients), end + 40)
                        context = ingredients[ctx_start:ctx_end]
                        audit_log.write(f"[{allergy.upper()}] Trigger: '{matched_text}' | Context: ...{context}...\n")

    # Print Final Impact Analysis
    print("\n" + "="*50)
    print("📊 CORPUS IMPACT REPORT (OFF Records)")
    print("="*50)
    
    # Sort allergies by total hits
    for allergy, count in sorted(hit_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"\n🚨 {allergy.upper()}: {count:,} total hits")
        
        # Sort the triggers within each allergy by frequency
        top_triggers = sorted(trigger_frequencies[allergy].items(), key=lambda x: x[1], reverse=True)[:50]
        for word, freq in top_triggers:
            print(f"   -> '{word}': {freq:,} times")

if __name__ == "__main__":
    keyword_processor = load_tripwires_flashtext('tests/fixtures/EU_allergens_multilingual.json')
    run_corpus_analysis('/var/lib/mysql-files/off_ingredients_dump.txt', keyword_processor)
