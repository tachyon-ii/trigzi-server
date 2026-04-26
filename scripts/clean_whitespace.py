#!/usr/bin/env python3
"""
=============================================================================
Module:        Whitespace Cleanup
Location:      scripts/clean_whitespace.py
Description:   Mechanical whitespace fixer for Python source files.

               Three fixes per file (in order):
                 1. Strip trailing whitespace from every line  (pylint C0303)
                 2. Remove trailing blank lines                (pylint C0305)
                 3. Ensure the file ends with exactly one \\n  (POSIX-correct)

               Idempotent — running twice produces the same result as once.
               Safe to run repeatedly as part of a pre-commit workflow.

Usage:
               ./scripts/clean_whitespace.py                # walk repo, fix in place
               ./scripts/clean_whitespace.py --dry-run      # preview only
               ./scripts/clean_whitespace.py path/to/file.py    # single file
               ./scripts/clean_whitespace.py core/ tests/   # specific directories
               ./scripts/clean_whitespace.py --include "*.md,*.json" core/  # custom patterns

Defaults:
               - Walks current working directory recursively
               - Targets *.py files
               - Skips hidden directories, venv/, __pycache__/, node_modules/,
                 .git/, build/, dist/, *.egg-info/

Exit codes:
               0  no changes needed (or dry-run with no changes pending)
               1  changes made (or dry-run with changes pending)
               2  invocation error (bad path, etc.)
=============================================================================
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_INCLUDE_PATTERNS = ["*.py"]

# Directories we never descend into. Edit-friendly: add or remove freely.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
}

# Directory name suffixes to skip (covers e.g. anything.egg-info)
SKIP_SUFFIXES = {".egg-info"}


# ---------------------------------------------------------------------------
# Core fix
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """
    Apply all three fixes to a string. Pure function — testable in isolation.

      1. Strip trailing whitespace from each line
      2. Drop trailing blank lines
      3. Ensure exactly one trailing newline

    Empty input returns empty (no spurious newline added to a 0-byte file).
    """
    if not text:
        return ""

    # Split preserving line semantics. splitlines() drops the trailing newline
    # (if any) and the final element is the post-final-newline content
    # (usually empty); we don't need to track that — we always normalise EOF.
    lines = text.splitlines()

    # 1. Strip trailing whitespace from every line.
    lines = [line.rstrip() for line in lines]

    # 2. Drop trailing blank lines.
    while lines and lines[-1] == "":
        lines.pop()

    # 3. Reassemble with exactly one trailing newline.
    if not lines:
        # File was entirely whitespace/blank — preserve as a single newline
        # rather than empty file, since most tools expect SOMETHING.
        # Argument either way; this is the conservative choice.
        return "\n"

    return "\n".join(lines) + "\n"


def diff_summary(original: str, cleaned: str) -> dict:
    """
    Compute a one-pass summary of what cleaning changed. Used in dry-run
    output and the final report. Doesn't reproduce the cleaning logic —
    just compares input and output.
    """
    if original == cleaned:
        return {"changed": False, "lines_stripped": 0,
                "trailing_blanks_removed": 0, "eof_normalized": False}

    orig_lines  = original.splitlines()
    clean_lines = cleaned.splitlines()

    # Count lines whose pre-clean rstrip would have changed something.
    # Compare line-by-line up to the shorter length — anything past that
    # was a trailing blank line we removed.
    lines_stripped = 0
    for o in orig_lines:
        if o != o.rstrip():
            lines_stripped += 1

    # Trailing blank line count: orig blanks at end minus clean blanks at end.
    def _trailing_blanks(lines: list[str]) -> int:
        n = 0
        for line in reversed(lines):
            if line.rstrip() == "":
                n += 1
            else:
                break
        return n

    blanks_removed = _trailing_blanks(orig_lines) - _trailing_blanks(clean_lines)

    # EOF newline normalisation: did we change whether the file ends in \n?
    orig_ends_newline  = original.endswith("\n")
    clean_ends_newline = cleaned.endswith("\n")
    eof_normalized = orig_ends_newline != clean_ends_newline

    return {
        "changed":                 True,
        "lines_stripped":          lines_stripped,
        "trailing_blanks_removed": max(blanks_removed, 0),
        "eof_normalized":          eof_normalized,
    }


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _should_skip_dir(path: Path) -> bool:
    """Return True if a directory should not be descended into."""
    if path.name.startswith("."):
        return True
    if path.name in SKIP_DIRS:
        return True
    if any(path.name.endswith(suf) for suf in SKIP_SUFFIXES):
        return True
    return False


def find_files(roots: list[Path], patterns: list[str]) -> Iterator[Path]:
    """
    Walk all roots, yielding files that match any of the patterns. Single
    files passed as roots are yielded directly (regardless of pattern) so
    `clean_whitespace.py path/to/specific.py` always processes that file.
    """
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if not root.exists():
            print(f"⚠  {root}: not found", file=sys.stderr)
            continue
        if root.is_file():
            if root not in seen:
                seen.add(root)
                yield root
            continue
        # Directory walk.
        for path in _walk(root, patterns):
            if path not in seen:
                seen.add(path)
                yield path


def _walk(root: Path, patterns: list[str]) -> Iterator[Path]:
    """Recursive walk, honouring SKIP_DIRS, yielding pattern matches."""
    try:
        entries = sorted(root.iterdir())
    except PermissionError:
        return
    for entry in entries:
        if entry.is_dir():
            if _should_skip_dir(entry):
                continue
            yield from _walk(entry, patterns)
        elif entry.is_file():
            if any(entry.match(pat) for pat in patterns):
                yield entry


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_file(path: Path, dry_run: bool, verbose: bool) -> dict | None:
    """
    Read, clean, and (unless dry-run) write back. Returns the diff_summary
    dict if the file changed, None if it was already clean. Returns None
    on a read error too — error already logged to stderr.
    """
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"⚠  {path}: not UTF-8, skipping", file=sys.stderr)
        return None
    except OSError as e:
        print(f"⚠  {path}: read error — {e}", file=sys.stderr)
        return None

    cleaned = clean_text(original)
    summary = diff_summary(original, cleaned)

    if not summary["changed"]:
        if verbose:
            print(f"   {path}: clean")
        return None

    if not dry_run:
        try:
            path.write_text(cleaned, encoding="utf-8")
        except OSError as e:
            print(f"⚠  {path}: write error — {e}", file=sys.stderr)
            return None

    # One-line summary per modified file
    bits = []
    if summary["lines_stripped"]:
        bits.append(f"{summary['lines_stripped']} line(s) stripped")
    if summary["trailing_blanks_removed"]:
        bits.append(f"{summary['trailing_blanks_removed']} trailing blank(s)")
    if summary["eof_normalized"]:
        bits.append("EOF newline normalised")
    detail = ", ".join(bits) or "whitespace changes"
    prefix = "would fix" if dry_run else "fixed"
    print(f"  {prefix}: {path}  ({detail})")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    "The CLI interface"
    ap = argparse.ArgumentParser(
        description="Strip trailing whitespace and normalise EOF newlines in source files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./scripts/clean_whitespace.py                       # fix the whole repo\n"
            "  ./scripts/clean_whitespace.py --dry-run             # preview\n"
            "  ./scripts/clean_whitespace.py core/llm/probe.py     # one file\n"
            "  ./scripts/clean_whitespace.py core/ tests/          # subset\n"
            "  ./scripts/clean_whitespace.py --include '*.py,*.md' # add markdown\n"
        ),
    )
    ap.add_argument("paths", nargs="*", default=["."],
                    help="Files or directories to clean (default: current directory).")
    ap.add_argument("--dry-run", "-n", action="store_true",
                    help="Show what would change without modifying files.")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Also list files that were already clean.")
    ap.add_argument("--include", default=",".join(DEFAULT_INCLUDE_PATTERNS),
                    help=f"Comma-separated glob patterns to include "
                         f"(default: {','.join(DEFAULT_INCLUDE_PATTERNS)}).")
    args = ap.parse_args()

    patterns = [p.strip() for p in args.include.split(",") if p.strip()]
    if not patterns:
        print("❌ No include patterns specified.", file=sys.stderr)
        return 2

    roots = [Path(p) for p in args.paths]

    mode = "DRY RUN — no files will be modified" if args.dry_run else "WRITING in place"
    print(f"── Whitespace cleanup ({mode}) ──")
    print(f"   Roots:    {', '.join(str(r) for r in roots)}")
    print(f"   Patterns: {', '.join(patterns)}")
    print()

    files_seen     = 0
    files_changed  = 0

    for path in find_files(roots, patterns):
        files_seen += 1
        result = process_file(path, dry_run=args.dry_run, verbose=args.verbose)
        if result is not None:
            files_changed += 1

    print()
    print("── Summary ──")
    print(f"   Files scanned:  {files_seen}")
    print(f"   Files changed:  {files_changed}" if not args.dry_run else
          f"   Files needing changes:  {files_changed}")

    if files_seen == 0:
        print("⚠  No files matched. Check paths and --include patterns.",
              file=sys.stderr)
        return 2

    # Exit 1 if there was anything to do (lets you wire into pre-commit)
    return 1 if files_changed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
