#!/usr/bin/env python3
from __future__ import annotations
"""
utils/tree.py

Clean project tree — excludes venv, caches, IDE noise and build artefacts.

Usage:
    ./utils/tree.py
    ./utils/tree.py /some/other/path
    ./utils/tree.py --dirs-only
"""

import os
import sys
import argparse

EXCLUDE_DIRS = {
    'venv', '.venv', 'env', '.env',
    '__pycache__', '.pytest_cache', '.mypy_cache',
    'node_modules', '.git', '.idea', '.vscode',
    'dist', 'build', '*.egg-info',
}

EXCLUDE_FILES = {
    '.DS_Store', 'Thumbs.db', '__init__.py',
    '*.pyc', '*.pyo', '*.pyd',
}


def excluded_dir(name: str) -> bool:
    if name in EXCLUDE_DIRS:
        return True
    if name.endswith('.egg-info'):
        return True
    return False


def excluded_file(name: str) -> bool:
    if name in EXCLUDE_FILES:
        return True
    if name.endswith(('.pyc', '.pyo', '.pyd')):
        return True
    return False


def tree(root: str, prefix: str = '', dirs_only: bool = False) -> None:
    try:
        entries = sorted(os.scandir(root), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    entries = [
        e for e in entries
        if not (e.is_dir() and excluded_dir(e.name))
        and not (e.is_file() and excluded_file(e.name))
        and not (dirs_only and e.is_file())
    ]

    for i, entry in enumerate(entries):
        is_last    = i == len(entries) - 1
        connector  = '└── ' if is_last else '├── '
        extension  = '    ' if is_last else '│   '

        print(f"{prefix}{connector}{entry.name}")

        if entry.is_dir():
            tree(entry.path, prefix + extension, dirs_only)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Clean project tree.")
    parser.add_argument('root', nargs='?', default='.', help="Root directory (default: .)")
    parser.add_argument('--dirs-only', action='store_true', help="Show directories only")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    print(root)
    tree(root, dirs_only=args.dirs_only)
