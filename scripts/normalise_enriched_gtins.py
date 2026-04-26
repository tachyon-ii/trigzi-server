#!/usr/bin/env python3
"""
=============================================================================
Module:        Enriched GTIN Normaliser
Location:      scripts/normalise_enriched_gtins.py
Description:   Normalises every GTIN in enriched_products.jsonl to the
               13-digit canonical form via utils/gtin.normalise(). Records
               with GTINs that fail normalisation are dropped from the
               output and reported on stderr.

Architecture Note:
A one-shot data-cleaning pass over the enriched JSONL — run it after
the bulk enrichment job to make every record's gtin field uniform
before importing into MariaDB. The script is non-destructive: it writes
to a separate file (default suffix _normalised.jsonl) so the original
input is preserved for replay.

Usage:
    ./scripts/normalise_enriched_gtins.py --input /data2000/enriched_products.jsonl
    ./scripts/normalise_enriched_gtins.py \\
        --input  /data2000/enriched_products.jsonl \\
        --output /data2000/enriched_products_normalised.jsonl
=============================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from utils.gtin import normalise
except ImportError:
    # Allow running from project root without installing the package
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from utils.gtin import normalise


def run(input_path: str, output_path: str) -> None:
    """Stream the input JSONL, normalise each record's GTIN, and write the survivors out."""
    print(f"Input  : {input_path}")
    print(f"Output : {output_path}\n")

    processed = 0
    written   = 0
    skipped   = 0
    changed   = 0

    with open(input_path,  'r', encoding='utf-8') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue

            processed += 1

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  Skip line {processed}: {e}")
                skipped += 1
                continue

            raw_gtin = str(record.get('gtin', '')).strip()
            gtin     = normalise(raw_gtin)

            if not gtin:
                print(f"  INVALID: {raw_gtin!r}")
                skipped += 1
                continue

            if gtin != raw_gtin:
                changed += 1
                record['gtin']       = gtin
                record['_source_id'] = gtin

            fout.write(json.dumps(record, ensure_ascii=False) + '\n')
            written += 1

    print(f"Processed : {processed:,}")
    print(f"Written   : {written:,}")
    print(f"Skipped   : {skipped:,}  (invalid GTIN)")
    print(f"Changed   : {changed:,}  (GTIN normalised)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Normalise GTINs in enriched_products.jsonl to 13 digits."
    )
    parser.add_argument('--input',  required=True,  help="Input JSONL path")
    parser.add_argument('--output', default=None,   help="Output JSONL path (default: input + .normalised)")
    args = parser.parse_args()

    output = args.output or args.input.replace('.jsonl', '_normalised.jsonl')
    run(args.input, output)
