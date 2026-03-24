#!/usr/bin/env python3
# utils/off_lookup.py
"""
OFF suffix tree GTIN lookup.

Resolves a GTIN to its sharded JSON file using the same 2-level suffix
scheme written by off_sharder.py:

    {base_dir}/{last_2}/{prev_2}/{gtin}.json

Examples:
    9352042000342  →  off/42/03/9352042000342.json
    00010191       →  off/91/01/00010191.json

Usage as a module:
    from utils.off_lookup import OFFLookup

    lookup = OFFLookup("/www/off")
    record = lookup.get("9352042000342")   # dict or None

Usage as a script:
    python utils/off_lookup.py 9352042000342
    python utils/off_lookup.py 9352042000342 --base /www/off
"""

import os
import json
import argparse
from typing import Optional


class OFFLookup:
    """
    O(1) GTIN lookup against a sharded Open Food Facts suffix tree.
    """

    def __init__(self, base_dir: str = "/www/off"):
        self.base_dir = base_dir

    def get(self, gtin: str) -> Optional[dict]:
        """
        Return the normalised OFF record for a GTIN, or None if not found.

        Args:
            gtin: GTIN string — digits only, any length.

        Returns:
            Parsed JSON dict or None.
        """
        path = self._path(gtin)
        if path is None:
            return None

        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def exists(self, gtin: str) -> bool:
        """Return True if a record exists for this GTIN."""
        path = self._path(gtin)
        return path is not None and os.path.exists(path)

    def path(self, gtin: str) -> Optional[str]:
        """Return the expected file path for a GTIN (whether or not it exists)."""
        return self._path(gtin)

    # MARK: - Private

    def _path(self, gtin: str) -> Optional[str]:
        """
        Calculate the shard path for a GTIN.
        Mirrors get_shard_path() in off_sharder.py exactly.

        Returns None if the GTIN is not a digit string.
        """
        if not gtin or not isinstance(gtin, str):
            return None

        # Strip whitespace; accept leading zeros
        gtin = gtin.strip()
        if not gtin.isdigit():
            return None

        safe  = gtin.zfill(4)
        l1    = safe[-2:]       # last 2 digits
        l2    = safe[-4:-2]     # previous 2 digits

        return os.path.join(self.base_dir, l1, l2, f"{gtin}.json")


# Module-level default instance — import and use directly:
#   from utils.off_lookup import lookup
#   record = lookup.get("9352042000342")
lookup = OFFLookup()


# MARK: - CLI

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Look up a GTIN in the OFF suffix tree."
    )
    parser.add_argument("gtin",  help="GTIN to look up")
    parser.add_argument("--base", default="/www/off",
                        help="Base directory of the OFF shard tree (default: /www/off)")
    parser.add_argument("--path-only", action="store_true",
                        help="Print the resolved file path and exit")
    args = parser.parse_args()

    l = OFFLookup(args.base)

    if args.path_only:
        print(l.path(args.gtin))
    else:
        record = l.get(args.gtin)
        if record:
            print(json.dumps(record, indent=2, ensure_ascii=False))
        else:
            print(f"❌ Not found: {args.gtin}")
            print(f"   Expected: {l.path(args.gtin)}")
