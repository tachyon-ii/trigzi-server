#!/usr/bin/env python3
"""
off_profiler.py

Streams an uncompressed Open Food Facts JSONL dump and profiles the schema.
Identifies average record size, reliable JSON paths, and data sparsity.
"""

import json
import argparse
from collections import Counter
import sys

def flatten_dict(d, parent_key='', sep='.'):
    """Recursively flatten nested dictionaries for path analysis."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def profile_off_data(filepath: str, sample_size: int):
    print(f"🔍 Profiling first {sample_size} records of {filepath}...")
    
    key_frequencies = Counter()
    total_bytes = 0
    records_processed = 0
    
    try:
        # Standard fast I/O read for uncompressed files
        with open(filepath, 'rt', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                    
                total_bytes += len(line.encode('utf-8'))
                
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(f"\n⚠️ Corrupt JSON on line {records_processed + 1}")
                    continue
                
                # Flatten the record to count nested keys
                flat_record = flatten_dict(record)
                
                for key in flat_record.keys():
                    key_frequencies[key] += 1
                    
                records_processed += 1
                
                if records_processed % 1000 == 0:
                    sys.stdout.write(f"\r  ... processed {records_processed} records")
                    sys.stdout.flush()
                    
                if records_processed >= sample_size:
                    break
                    
    except FileNotFoundError:
        print(f"\n❌ File not found: {filepath}")
        sys.exit(1)

    print("\n\n📊 --- Profiling Results ---")
    print(f"Records Analyzed: {records_processed}")
    print(f"Average Record Size: {(total_bytes / records_processed) / 1024:.2f} KB")
    print(f"Total Unique JSON Paths: {len(key_frequencies)}")
    
    print("\n🔑 --- Top 30 Most Frequent Keys ---")
    for key, count in key_frequencies.most_common(30):
        percentage = (count / records_processed) * 100
        print(f"  {key:<40} {percentage:>6.1f}%  ({count})")
        
    print("\n🎯 --- Targeted Clinical Field Check ---")
    targets = [
        "code", 
        "product_name", 
        "ingredients_text", 
        "ingredients_text_en", 
        "image_url",
        "nutriments.energy-kcal_100g",
        "nutriments.sugars_100g"
    ]
    for t in targets:
        percentage = (key_frequencies[t] / records_processed) * 100 if records_processed else 0
        print(f"  {t:<40} {percentage:>6.1f}%")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Profile Open Food Facts JSONL.")
    parser.add_argument("filepath", help="Path to unzipped OFF jsonl dump")
    parser.add_argument("--samples", type=int, default=10000, help="Number of records to analyze")
    args = parser.parse_args()
    
    profile_off_data(args.filepath, args.samples)
