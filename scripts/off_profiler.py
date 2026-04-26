#!/usr/bin/env python3
"""
=============================================================================
Module:        Open Food Facts Schema Profiler
Location:      scripts/off_profiler.py
Description:   Profiles the Open Food Facts JSONL dump schema. For each
               field of interest, computes:
                 - Presence % (how many records have the field non-empty)
                 - Type distribution
                 - Sample values (to understand actual format)

               Output is written to logs/off_schema_profile.txt for
               reference when writing the OFF importer.

Architecture Note:
This is a one-shot diagnostic, not part of the runtime hot path. Run it
when the OFF dump format is uncertain or after upstream schema changes.
The FIELDS_OF_INTEREST dict at module scope is the single source of
truth for which fields are surveyed; add new groups there.

Usage:
    ./scripts/off_profiler.py /data2000/openfoodfacts-products.jsonl
    ./scripts/off_profiler.py /data2000/openfoodfacts-products.jsonl --samples 50000
=============================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'off_schema_profile.txt')

# Fields we care about — grouped by purpose
FIELDS_OF_INTEREST = {
    'identity': [
        'code',
        'product_name',
        'product_name_en',
        'brands',
        'quantity',
    ],
    'image': [
        'image_url',
        'image_front_url',
        'image_front_small_url',
        'images',
    ],
    'ingredients': [
        'ingredients_text',
        'ingredients_text_en',
        'allergens',
        'allergens_tags',
        'traces_tags',
    ],
    'nutrition': [
        'nutriments.energy_100g',
        'nutriments.energy-kcal_100g',
        'nutriments.energy-kj_100g',
        'nutriments.proteins_100g',
        'nutriments.fat_100g',
        'nutriments.saturated-fat_100g',
        'nutriments.carbohydrates_100g',
        'nutriments.sugars_100g',
        'nutriments.fiber_100g',
        'nutriments.sodium_100g',
        'nutriments.salt_100g',
    ],
    'serving': [
        'serving_size',
        'serving_quantity',
        'nutriments.energy_serving',
    ],
    'classification': [
        'categories',
        'categories_tags',
        'food_groups',
        'nova_group',
        'nutriscore_grade',
        'ecoscore_grade',
    ],
    'metadata': [
        'countries_tags',
        'lang',
        'states_tags',
        'data_quality_tags',
    ],
    'quality': [
        'data_quality_errors_tags',
        'data_quality_warnings_tags',
        'images',
    ],
}


def get_nested(record: dict, path: str) -> Any:
    """Get a value from a nested path like 'nutriments.energy_100g'."""
    parts = path.split('.', 1)
    val = record.get(parts[0])
    if len(parts) == 1:
        return val
    if isinstance(val, dict):
        return get_nested(val, parts[1])
    return None


def is_present(val: Any) -> bool:
    """True if the value is meaningfully present (not None/empty/zero-string)."""
    if val is None:
        return False
    if isinstance(val, str):
        return val.strip() not in ('', 'null', 'NULL', 'unknown', '[]', '{}')
    if isinstance(val, (list, dict)):
        return len(val) > 0
    return True


def value_summary(samples: list) -> str:
    """Summarise a list of sample values."""
    if not samples:
        return '(none)'
    # Show up to 3 unique examples
    seen = []
    for s in samples:
        r = repr(s)[:60]
        if r not in seen:
            seen.append(r)
        if len(seen) >= 3:
            break
    return ' | '.join(seen)


def profile(filepath: str, sample_size: int, output_path: str) -> None:  # pylint: disable=too-many-statements
    """Stream-read the OFF JSONL dump and emit a per-field schema profile.

    Walks at most ``sample_size`` records, accumulating presence counts,
    type distributions, and a handful of example values for every field
    listed in FIELDS_OF_INTEREST. Renders the result both to stdout and
    to ``output_path``.

    The body is intentionally linear (parse → accumulate → render → write)
    rather than decomposed into helpers, since the per-record work is
    cheap and threading state through helper functions would obscure the
    streaming flow without saving meaningful complexity.
    """
    print(f"Profiling: {filepath}")
    print(f"Samples  : {sample_size}")
    print(f"Output   : {output_path}\n")

    # Stats per field
    presence  = defaultdict(int)   # count of records where field is present
    type_dist = defaultdict(Counter)
    samples   = defaultdict(list)

    total       = 0
    total_bytes = 0
    t0          = time.time()

    all_fields = [f for group in FIELDS_OF_INTEREST.values() for f in group]

    with open(filepath, 'rt', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            total_bytes += len(line.encode('utf-8'))

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            for field in all_fields:
                val = get_nested(record, field)
                if is_present(val):
                    presence[field] += 1
                    type_dist[field][type(val).__name__] += 1
                    if len(samples[field]) < 5:
                        samples[field].append(val)

            total += 1

            if total % 10_000 == 0:
                elapsed = time.time() - t0
                rate    = total / elapsed
                sys.stdout.write(
                    f"\r  {total:,} records | "
                    f"{total_bytes/1e9:.1f} GB | "
                    f"{rate:.0f} rec/s"
                )
                sys.stdout.flush()

            if total >= sample_size:
                break

    elapsed = time.time() - t0
    avg_kb  = (total_bytes / total / 1024) if total else 0

    lines = []
    lines.append("OFF Schema Profile")
    lines.append(f"{'='*60}")
    lines.append(f"File     : {filepath}")
    lines.append(f"Records  : {total:,}")
    lines.append(f"Avg size : {avg_kb:.2f} KB/record")
    lines.append(f"Elapsed  : {elapsed:.1f}s ({total/elapsed:.0f} rec/s)")
    lines.append("")

    for group, fields in FIELDS_OF_INTEREST.items():
        lines.append(f"{'─'*60}")
        lines.append(f"  {group.upper()}")
        lines.append(f"{'─'*60}")
        for field in fields:
            pct   = presence[field] / total * 100 if total else 0
            types = ', '.join(f"{k}:{v}" for k, v in type_dist[field].most_common(3))
            ex    = value_summary(samples[field])
            lines.append(f"  {field:<45} {pct:>5.1f}%  [{types}]")
            lines.append(f"    ex: {ex}")
        lines.append("")

    output = '\n'.join(lines)

    # Print to stdout
    print(f"\n\n{output}")

    # Write to file
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output)

    print(f"\nWritten to: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Profile OFF JSONL schema — field presence, types, sample values."
    )
    parser.add_argument('filepath', help="Path to unzipped OFF JSONL dump")
    parser.add_argument('--samples', type=int, default=10_000,
                        help="Number of records to sample (default: 10000)")
    parser.add_argument('--output', default=OUTPUT_FILE,
                        help="Output file path")
    args = parser.parse_args()
    profile(args.filepath, args.samples, args.output)
