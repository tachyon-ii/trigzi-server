#!/usr/bin/env python3
"""
count_names.py — find placeholder/garbage product names in the shard tree.

Recurses the shard tree, counts occurrences of each product name,
and reports the top-k. Useful for identifying placeholder values
("Unknown Product", "", "null", etc.) that should be nulled out
or filtered during enrichment.

Usage:
    python3 count_names.py                          # top 20, all records
    python3 count_names.py --topk 50                # top 50
    python3 count_names.py --limit 100000           # sample first 100k files
    python3 count_names.py --dir /data2000/off --topk 30
"""

import os
import json
import argparse
import sys
from collections import Counter
from typing import Optional

DEFAULT_SHARD_DIR = "/data2000/off"
DEFAULT_TOPK      = 20


def count_names(shard_dir: str, limit: int, topk: int) -> None:
    counts   = Counter()
    total    = 0
    errors   = 0

    print(f"Scanning: {shard_dir}")
    print(f"Limit: {limit or 'unlimited'} | Top-k: {topk}\n")

    for root, dirs, files in os.walk(shard_dir):
        dirs.sort()
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue

            if limit and total >= limit:
                break

            path = os.path.join(root, fname)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    record = json.load(f)

                name = (
                    record.get("name") or
                    record.get("title") or
                    ""
                ).strip()

                counts[name] += 1
                total += 1

                if total % 200_000 == 0:
                    sys.stdout.write(f"\r  Scanned: {total:,}  ")
                    sys.stdout.flush()

            except (json.JSONDecodeError, OSError):
                errors += 1

        else:
            continue
        break

    print(f"\r  Scanned: {total:,} files | Errors: {errors}\n")
    print(f"{'Rank':<6} {'Count':>8}  {'%':>6}  Name")
    print("-" * 60)

    for rank, (name, count) in enumerate(counts.most_common(topk), 1):
        pct     = count / total * 100 if total else 0
        display = repr(name) if not name else name
        print(f"{rank:<6} {count:>8}  {pct:>5.2f}%  {display}")

    print(f"\n  Total unique names: {len(counts):,}")
    print(f"  Total records scanned: {total:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Count top-k product names in the shard tree."
    )
    parser.add_argument(
        "--dir", default=DEFAULT_SHARD_DIR,
        help=f"Shard tree root (default: {DEFAULT_SHARD_DIR})"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Stop after N files (0 = unlimited)"
    )
    parser.add_argument(
        "--topk", type=int, default=DEFAULT_TOPK,
        help=f"Number of top names to show (default: {DEFAULT_TOPK})"
    )
    args = parser.parse_args()
    count_names(args.dir, args.limit, args.topk)
