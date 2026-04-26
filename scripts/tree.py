#!/usr/bin/env python3
"""
=============================================================================
Module:        Project Tree Walker
Location:      scripts/tree.py
Description:   Clean project-tree printer. Walks the directory recursively
               and emits a `tree(1)`-style listing with venvs, caches, IDE
               clutter, and build artefacts elided so the output reflects
               the actual code layout. Noisy directories (logs, scans,
               LLM response archives) appear as a single line with their
               item count rather than being expanded.

Architecture Note:
Four module-level sets control behaviour:
  - EXCLUDE_DIRS — directories hidden entirely from the tree
  - LEAF_DIRS — directories shown as a single line with
    "(N files elided)" suffix; their contents are not descended.
    Used for archive-style directories.
  - PRUNE_FILES_DIRS — directories whose subdirectory structure is
    shown but whose files (at this level and recursively below) are
    suppressed. The flag propagates down through recursion.
  - EXCLUDE_FILES — filenames hidden wherever they appear
The walker is deliberately a recursive generator, not a generator-of-
generators, because the tree shape is small enough that simple recursion
stays readable and avoids state-threading.

Usage:
    ./scripts/tree.py
    ./scripts/tree.py /some/other/path
    ./scripts/tree.py --dirs-only
=============================================================================
"""

from __future__ import annotations

import argparse
import os

EXCLUDE_DIRS = {
    'venv', '.venv', 'env', '.env',
    '__pycache__', '.pytest_cache', '.mypy_cache',
    'node_modules', '.git', '.idea', '.vscode',
    'dist', 'build', '*.egg-info',
}

# Directories that appear as a single one-line leaf entry annotated with
# "(N files elided)" — never descended into. Used for archive-style
# directories where the existence and rough count matter but per-file
# enumeration would drown the rest of the tree.
LEAF_DIRS = {
    'llm_responses',
    'scans',
    'runs',
}

# Directories whose subdirectory structure is shown but whose files (at
# this level and recursively below) are suppressed. The flag propagates
# down through recursion: once we enter a PRUNE_FILES_DIRS member, every
# nested call hides files too. Useful for top-level "logs/" trees where
# you want the shape but not the file-by-file noise.
PRUNE_FILES_DIRS = {
    'logs',
}

EXCLUDE_FILES = {
    '.DS_Store', 'Thumbs.db', '__init__.py',
    '*.pyc', '*.pyo', '*.pyd',
}

def excluded_dir(name: str) -> bool:
    """True if a directory name should be hidden from the tree output."""
    if name in EXCLUDE_DIRS:
        return True
    if name.endswith('.egg-info'):
        return True
    return False


def excluded_file(name: str) -> bool:
    """True if a file name should be hidden from the tree output."""
    if name in EXCLUDE_FILES:
        return True
    if name.endswith(('.pyc', '.pyo', '.pyd')):
        return True
    return False


def _count_visible_files_recursive(dir_path: str) -> int:
    """Count visible files anywhere under dir_path, recursively.

    Used for the "(N files elided)" annotation on LEAF_DIRS entries —
    the count reflects every file the user *would* see if descent
    weren't suppressed, including files in nested subdirectories.
    """
    total = 0
    try:
        for entry in os.scandir(dir_path):
            if entry.is_dir():
                if excluded_dir(entry.name):
                    continue
                total += _count_visible_files_recursive(entry.path)
            elif entry.is_file():
                if not excluded_file(entry.name):
                    total += 1
    except (PermissionError, OSError):
        return total
    return total


def tree(root_path: str, prefix: str = '', dirs_only: bool = False,
         prune_files: bool = False) -> None:
    """Recursively print a tree-style listing of ``root_path``, eliding excluded entries.

    Two pruning modes layered on top of EXCLUDE_DIRS / EXCLUDE_FILES:

      * LEAF_DIRS — the directory itself shows as a single line with a
        "(N files elided)" suffix and is not descended into.
      * PRUNE_FILES_DIRS — the directory's subdirectory structure is
        shown, but files at that level and recursively below are
        suppressed. The flag propagates down once enabled, so
        ``logs/some/deep/path`` correctly hides files in every nested
        directory under ``logs/``.
    """
    try:
        entries = sorted(os.scandir(root_path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    entries = [
        e for e in entries
        if not (e.is_dir() and excluded_dir(e.name))
        and not (e.is_file() and excluded_file(e.name))
        and not (dirs_only and e.is_file())
        and not (prune_files and e.is_file())
    ]

    for i, entry in enumerate(entries):
        is_last    = i == len(entries) - 1
        connector  = '└── ' if is_last else '├── '
        extension  = '    ' if is_last else '│   '

        if entry.is_dir() and entry.name in LEAF_DIRS:
            count = _count_visible_files_recursive(entry.path)
            if count == 0:
                suffix = "  (empty)"
            elif count == 1:
                suffix = "  (1 file elided)"
            else:
                suffix = f"  ({count} files elided)"
            print(f"{prefix}{connector}{entry.name}{suffix}")
            continue

        print(f"{prefix}{connector}{entry.name}")

        if entry.is_dir():
            child_prune = prune_files or (entry.name in PRUNE_FILES_DIRS)
            tree(entry.path, prefix + extension, dirs_only, child_prune)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Clean project tree.")
    parser.add_argument('root', nargs='?', default='.', help="Root directory (default: .)")
    parser.add_argument('--dirs-only', action='store_true', help="Show directories only")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    print(root)
    tree(root, dirs_only=args.dirs_only)
