#!/usr/bin/env python3
from __future__ import annotations
#
#  scripts/tarzan.py
#
#  Swings through the codebase and concatenates every .py file
#  with a clear path header — one file representing the whole system.
#
#  Useful for:
#    - Pasting the full codebase into an LLM context window
#    - Sending to another AI instance for review
#    - Point-in-time snapshots of the system
#
#  Usage:
#    ./scripts/tarzan.py                        # stdout
#    ./scripts/tarzan.py > /tmp/system.py       # file
#    ./scripts/tarzan.py --root core            # subtree only
#    ./scripts/tarzan.py --exclude tests        # skip a dir
#

import os
import sys
import argparse
from datetime import datetime

# Force UTF-8 output regardless of terminal locale
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

EXCLUDE_DIRS = {
    'venv', '.venv', 'env', '.env',
    '__pycache__', '.pytest_cache', '.mypy_cache',
    'node_modules', '.git', '.idea', '.vscode',
    'dist', 'build',
}

EXCLUDE_FILES = {
    '__init__.py',
    'tarzan.py',   # don't include self
}


def excluded_dir(name: str) -> bool:
    return name in EXCLUDE_DIRS or name.endswith('.egg-info')


def excluded_file(name: str) -> bool:
    if name.endswith(".py"):
        return name in EXCLUDE_FILES
    else:
        return name


def collect(root: str, extra_exclude: set) -> list[str]:
    """Return sorted list of .py file paths under root."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs in-place so os.walk doesn't descend into them
        dirnames[:] = sorted(
            d for d in dirnames
            if not excluded_dir(d) and d not in extra_exclude
        )
        for filename in sorted(filenames):
            if not excluded_file(filename):
                paths.append(os.path.join(dirpath, filename))
    return paths


def header(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)
    bar = '=' * (len(rel) + 8)
    return f"\n{bar}\n#  {rel}\n{bar}\n"


def run(root: str, extra_exclude: set, output) -> None:
    paths = collect(root, extra_exclude)

    ts  = datetime.now().strftime('%Y-%m-%d %H:%M')
    output.write(f"# tarzan.py snapshot - {ts}\n")
    output.write(f"# root: {os.path.abspath(root)}\n")
    output.write(f"# files: {len(paths)}\n")

    for path in paths:
        output.write(header(path, root))
        try:
            with open(path, 'r', encoding='utf-8') as f:
                output.write(f.read())
        except Exception as e:
            output.write(f"# ERROR reading file: {e}\n")
        output.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Concatenate all .py files with path headers."
    )
    parser.add_argument(
        '--root', default='.',
        help="Root directory to scan (default: .)"
    )
    parser.add_argument(
        '--exclude', nargs='*', default=[],
        help="Additional directory names to exclude"
    )
    parser.add_argument(
        '--output', default=None,
        help="Write to file instead of stdout (avoids terminal encoding issues)"
    )
    args = parser.parse_args()

    extra = set(args.exclude or [])

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            run(os.path.abspath(args.root), extra, f)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        run(os.path.abspath(args.root), extra, sys.stdout)
