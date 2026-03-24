#!/usr/bin/env python3
"""
off_sharder.py

Purpose:
Ingests the massive Open Food Facts JSONL dump and shards it into a high-performance 
2-level suffix tree local database. 

Architecture:
- Streaming I/O: Processes line-by-line to guarantee a flat memory footprint regardless of file size.
- CDN Resolution: Dynamically calculates the OFF image CDN URL based on GTIN length and image revision.
- Schema Normalization: Strips out 95% of the OFF bloat (edit histories, OCR data) and extracts
  only the core clinical and metadata fields required for downstream LLM enrichment.
- Suffix Sharding: Routes to `data/raw/off/{last_2}/{prev_2}/{gtin}.json` to ensure O(1) 
  filesystem lookups across millions of records.
"""

import os
import sys
import json
import argparse
from typing import Optional

# --- CORE EXTRACTION LOGIC ---

def build_off_image_url(gtin: str, images_node: dict) -> Optional[str]:
    """
    Constructs the absolute CDN URL for the product's front image.
    OFF splits GTINs > 8 digits into 3/3/3/remainder paths to load balance their CDN.
    """
    if not images_node:
        return None
        
    front = images_node.get('selected', {}).get('front', {})
    if not front:
        return None

    # Prefer English if available, otherwise fallback to the first available language key
    lang = 'en' if 'en' in front else next(iter(front.keys()), None)
    if not lang:
        return None

    rev = front[lang].get('rev')
    if not rev:
        return None

    # Construct the GS1-safe routing path
    if len(gtin) > 8:
        path = f"{gtin[:3]}/{gtin[3:6]}/{gtin[6:9]}/{gtin[9:]}"
    else:
        path = gtin

    return f"https://images.openfoodfacts.org/images/products/{path}/front_{lang}.{rev}.400.jpg"

def extract_normalized_record(raw: dict) -> Optional[dict]:
    """
    Extracts only the high-value clinical data from the bloated OFF schema.
    Returns None if the record lacks a valid GTIN.
    """
    gtin = raw.get('code')
    if not gtin or not isinstance(gtin, str) or not gtin.isdigit():
        return None

    # Prioritize English fields, fallback to generic localized fields
    name = raw.get('product_name_en') or raw.get('product_name') or "Unknown Product"
    ingredients = raw.get('ingredients_text_en') or raw.get('ingredients_text') or ""
    
    nutriments = raw.get('nutriments', {})

    return {
        "gtin": gtin,
        "source": "openfoodfacts",
        "name": name,
        "brand": raw.get('brands', ''),
        "category": raw.get('categories', ''),
        "image_url": build_off_image_url(gtin, raw.get('images', {})),
        "ingredients_raw": ingredients,
        "nutrition_100g": {
            "energy_kcal": nutriments.get('energy-kcal_100g'),
            "protein_g": nutriments.get('proteins_100g'),
            "fat_total_g": nutriments.get('fat_100g'),
            "fat_saturated_g": nutriments.get('saturated-fat_100g'),
            "carbohydrates_g": nutriments.get('carbohydrates_100g'),
            "sugars_g": nutriments.get('sugars_100g'),
            "fiber_g": nutriments.get('fiber_100g'),
            "sodium_g": nutriments.get('sodium_100g') # OFF stores sodium in g, not mg
        }
    }

# --- FILESYSTEM SHARDING ---

def get_shard_path(base_dir: str, gtin: str) -> str:
    """
    Calculates the 2-level suffix directory path.
    Example: 9352042000342 -> {base_dir}/42/03/9352042000342.json
    """
    safe_gtin = gtin.zfill(4) 
    level_1 = safe_gtin[-2:]
    level_2 = safe_gtin[-4:-2]
    
    return os.path.join(base_dir, level_1, level_2)

# --- MAIN EXECUTION ---

def process_file(input_file: str, output_dir: str, limit: int):
    print(f"🚀 Starting OFF Sharder")
    print(f"📦 Input: {input_file}")
    print(f"🗂️ Output Tree: {output_dir}")
    if limit > 0:
        print(f"🛑 Limit: {limit} records")

    processed = 0
    saved = 0
    skipped = 0

    try:
        with open(input_file, 'rt', encoding='utf-8') as f_in:
            for line in f_in:
                if limit > 0 and processed >= limit:
                    break
                    
                if not line.strip():
                    continue

                processed += 1

                try:
                    raw_json = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue

                normalized = extract_normalized_record(raw_json)
                if not normalized:
                    skipped += 1
                    continue

                # Route and save
                target_dir = get_shard_path(output_dir, normalized['gtin'])
                os.makedirs(target_dir, exist_ok=True)
                
                target_file = os.path.join(target_dir, f"{normalized['gtin']}.json")
                
                with open(target_file, 'w', encoding='utf-8') as f_out:
                    json.dump(normalized, f_out, ensure_ascii=False)
                
                saved += 1

                # Progress indicator
                if processed % 5000 == 0:
                    sys.stdout.write(f"\r  ... Processed: {processed} | Saved: {saved} | Skipped: {skipped}")
                    sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n\n⚠️ Process interrupted by user.")
    except Exception as e:
        print(f"\n\n❌ Fatal Error: {e}")
        sys.exit(1)

    print(f"\n\n✅ Sharding Complete!")
    print(f"Total Processed: {processed}")
    print(f"Successfully Sharded: {saved}")
    print(f"Skipped (No GTIN/Corrupt): {skipped}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Shard Open Food Facts JSONL into a 2-level suffix tree.")
    parser.add_argument("--input", required=True, help="Path to unzipped OFF jsonl dump")
    parser.add_argument("--output", default="data/raw/off", help="Base directory for the sharded tree")
    parser.add_argument("--limit", type=int, default=0, help="Limit total records processed (for testing)")
    
    args = parser.parse_args()
    
    process_file(args.input, args.output, args.limit)
