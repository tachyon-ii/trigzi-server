#!/usr/bin/env python3
"""
generate_clean_frequencies.py
Streams the raw OFF database dump through the production ingredient parser.
Outputs a clean, frequency-sorted TSV of true semantic tokens.
"""

import sys
import os
import time
from collections import Counter

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.ingredient_parser import parse_ingredients

INPUT_FILE = "/var/lib/mysql-files/raw_ingredients_dump.txt"
OUTPUT_FILE = "clean_ingredient_frequencies.tsv"

def main():
    counter = Counter()
    lines_processed = 0
    start_time = time.time()

    print(f"📖 Streaming raw MySQL dump from {INPUT_FILE}...")
    print("🧠 Engaging Production Lexer. This may take a few minutes...")
    
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # The magic happens here: true semantic tokenization
                tokens = parse_ingredients(line)

                # Tally the cleaned tokens
                for t in tokens:
                    counter[t] += 1

                lines_processed += 1
                
                # Progress monitor
                if lines_processed % 50000 == 0:
                    elapsed = time.time() - start_time
                    rate = lines_processed / elapsed
                    print(f"   ...parsed {lines_processed:,} lines ({rate:.0f} lines/sec)")

    except FileNotFoundError:
        print(f"❌ Could not find {INPUT_FILE}")
        sys.exit(1)

    elapsed = time.time() - start_time
    print(f"\n✅ Finished parsing {lines_processed:,} lines in {elapsed:.1f} seconds.")
    print(f"💾 Writing {len(counter):,} unique semantic tokens to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as out_f:
        # Export sorted by highest frequency first (Zipf distribution)
        for token, count in counter.most_common():
            out_f.write(f"{token}\t{count}\n")

    print("🎉 Clean Frequency Generation Complete.")

if __name__ == "__main__":
    main()
