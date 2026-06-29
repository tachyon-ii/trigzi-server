#!/usr/bin/env python3
"""
=============================================================================
Module:        Codebase Concatenator (Tarzan)
Location:      scripts/tarzan.py
Description:   Two-mode codebase tool. The default mode swings through the
               codebase and concatenates every text file with a clear path
               header — one stream representing the whole system. The
               --map mode emits a structured project map with banner
               docstrings, top-level declarations (classes / functions /
               module constants), and resource-file schema summaries —
               for quickly bootstrapping a fresh LLM instance into the
               project's shape without ingesting the full source.

Architecture Note:
Snapshot mode policy lives in four module-level sets:
  - INCLUDE_EXTENSIONS — file extensions treated as text-of-interest
  - EXCLUDE_DIRS — directory names skipped wherever they appear
  - EXCLUDE_FILES — filenames skipped regardless of directory
  - DIR_REJECT_EXTENSIONS — per-directory content-type rejection
    (e.g. html/ keeps web assets but rejects .md/.txt/.py/.bin)

Map mode adds two more:
  - MAP_INCLUDE_DIRS — directories scanned for resource summaries (JSON
    schemas, prompt previews). Each entry is a path relative to --root.
  - MAP_RESOURCE_EXTS — which file types in those dirs get summarised.

Tree generation:
  The project tree is emitted in BOTH snapshot and map mode. It is built
  by the inline _tree_walk / generate_tree pair, which reads root/.gitignore
  (if present) and uses it to prune the display — this is the main reason
  for a custom tree rather than shelling out to system tree(1), which has
  no awareness of project-specific ignores. No subprocess dependency.

  The boundary is deliberate: .gitignore governs what appears in the tree
  (large data files, external sources, generated artefacts), while
  MAP_INCLUDE_DIRS governs what the resource scanner summarises. Many
  resource files are gitignored precisely because of their size or origin,
  but their schema is still useful context in the map.

The map's structural extraction uses the standard library ast module —
no third-party deps.

Usage:
    ./scripts/tarzan.py                            # snapshot to stdout
    ./scripts/tarzan.py > /tmp/system.py           # snapshot to file
    ./scripts/tarzan.py --root core                # subtree only
    ./scripts/tarzan.py --exclude tests            # skip a dir
    ./scripts/tarzan.py --map                      # structured map to stdout
    ./scripts/tarzan.py --map --output map.md      # structured map to file
=============================================================================
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import sys
from datetime import datetime

# Force UTF-8 output regardless of terminal locale
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ─── Snapshot-mode policy ────────────────────────────────────────────────────

INCLUDE_EXTENSIONS = {'.py', '.md', '.txt', '.json'}

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

# Per-directory content-type rejections. A file under any of these
# directories (recursively) whose extension matches the listed set is
# excluded from the snapshot. Use this to keep a directory's web/data
# assets while rejecting accidentally-dumped source-code or snapshot
# artefacts (e.g. ``html/`` should serve .html/.css/.js/.json/.svg but
# never carry a tarzan-style .txt or .md dump).
DIR_REJECT_EXTENSIONS = {
    'html': {'.md', '.txt', '.py', '.bin'},
    'logs': {'.txt', '.log', '.jsonl'},
}

# ─── Map-mode policy ─────────────────────────────────────────────────────────

# Directories scanned for resource-schema summaries (relative to --root).
# Intentionally independent of .gitignore: large data files and external
# corpora are often gitignored because of their size or origin, but their
# schema is still useful context in the map.
MAP_INCLUDE_DIRS = [
    'data',
    'prompts',
    'tests/fixtures',
    'core/llm',           # picks up llm_providers.json
]

# Which file types in those dirs get a one-line summary in the map.
MAP_RESOURCE_EXTS = ('.json', '.jsonl', '.txt')


# ─── Snapshot-mode helpers ────────────────────────────────────────────────────


def excluded_dir(name: str) -> bool:
    """True if the directory should be skipped wholesale during the walk."""
    return name in EXCLUDE_DIRS or name.endswith('.egg-info')


def excluded_file(name: str) -> bool:
    """True if the file should be skipped — either wrong extension, or in the blocklist."""
    _, ext = os.path.splitext(name)
    if ext not in INCLUDE_EXTENSIONS:
        return True
    return name in EXCLUDE_FILES


def dir_rejects_file(rel_path: str) -> bool:
    """True if any ancestor directory of rel_path rejects this file's extension."""
    _, ext = os.path.splitext(rel_path)
    if not ext:
        return False
    parts = rel_path.replace('\\', '/').split('/')
    for ancestor in parts[:-1]:
        rejects = DIR_REJECT_EXTENSIONS.get(ancestor)
        if rejects and ext in rejects:
            return True
    return False


def collect(root: str, extra_exclude: set) -> list[str]:
    """Return sorted list of file paths under root, applying exclude rules."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not excluded_dir(d) and d not in extra_exclude
        )
        for filename in sorted(filenames):
            if excluded_file(filename):
                continue
            full_path = os.path.join(dirpath, filename)
            rel_path  = os.path.relpath(full_path, root)
            if dir_rejects_file(rel_path):
                continue
            paths.append(full_path)
    return paths


def header(path: str, root: str) -> str:
    """Render the per-file boundary marker that precedes each concatenated body."""
    rel = os.path.relpath(path, root)
    try:
        size = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='replace') as fp:
            line_count = sum(1 for _ in fp)
        meta = f"<!--- SIZE: {size:,} bytes · {line_count:,} lines --->"
    except OSError:
        meta = "<!--- SIZE: unknown --->"

    return (
        "\n<!--- BEGIN FILE --->\n"
        f"<!--- PATH: {rel} --->\n"
        f"{meta}\n\n"
    )


def run_snapshot(root: str, extra_exclude: set, output) -> None:
    """Walk root, concatenate every included file's contents to output with markers."""
    paths = collect(root, extra_exclude)

    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    output.write("<!--- TARZAN SNAPSHOT --->\n")
    output.write(f"<!--- GENERATED: {ts} --->\n")
    output.write(f"<!--- ROOT: {os.path.abspath(root)} --->\n")
    output.write(f"<!--- FILES: {len(paths)} --->\n")

    # Project tree — present in both snapshot and map mode.
    tree_text = generate_tree(root)
    if tree_text:
        output.write("\n<!--- PROJECT TREE --->\n")
        output.write("```\n")
        output.write(tree_text.rstrip())
        output.write("\n```\n")

    for path in paths:
        output.write(header(path, root))
        try:
            with open(path, 'r', encoding='utf-8') as fp:
                output.write(fp.read())
        except OSError as e:
            output.write(f"<!--- ERROR reading file: {e} --->\n")
        output.write('\n')


# ─── .gitignore parser ────────────────────────────────────────────────────────


def _load_gitignore(root: str) -> list[str]:
    """Return non-comment, non-empty lines from root/.gitignore, or []."""
    path = os.path.join(root, '.gitignore')
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return [
                ln.rstrip('\n')
                for ln in f
                if ln.strip() and not ln.startswith('#')
            ]
    except OSError:
        return []


def _gitignore_matcher(patterns: list[str], root: str):
    """Return a callable(abs_path, is_dir) -> bool that applies gitignore patterns.

    Handles the subset of gitignore syntax that appears in typical server
    .gitignore files:
      - Plain names:    logs        matches any entry named "logs" anywhere
      - Glob patterns:  *.pyc       matched against the bare name
      - Dir patterns:   data/       only matches directories
      - Rooted:         /foo        only matches at the root level
      - Negations:      !important  re-includes a previously excluded path
                        (applied in order; last match wins, per git semantics)

    Double-star (**) and character-class ([abc]) patterns are not implemented
    — they don't appear in typical server .gitignore files and the added
    complexity isn't warranted for a display tool.
    """
    abs_root = os.path.abspath(root)

    # Compile each raw line into (negated, dir_only, rooted, pattern)
    compiled: list[tuple[bool, bool, bool, str]] = []
    for raw in patterns:
        negated  = raw.startswith('!')
        stripped = raw.lstrip('!')
        dir_only = stripped.endswith('/')
        pattern  = stripped.rstrip('/')
        rooted   = pattern.startswith('/')
        if rooted:
            pattern = pattern.lstrip('/')
        compiled.append((negated, dir_only, rooted, pattern))

    def matches(abs_path: str, is_dir: bool) -> bool:
        rel  = os.path.relpath(abs_path, abs_root).replace('\\', '/')
        name = os.path.basename(abs_path)
        result = False
        for negated, dir_only, rooted, pattern in compiled:
            if dir_only and not is_dir:
                continue
            if rooted:
                hit = fnmatch.fnmatch(rel, pattern) or rel == pattern
            else:
                # Match against bare name OR any path segment
                hit = fnmatch.fnmatch(name, pattern) or any(
                    fnmatch.fnmatch(part, pattern)
                    for part in rel.split('/')
                )
            if hit:
                result = not negated
        return result

    return matches


# ─── Inline tree walker ───────────────────────────────────────────────────────


def _tree_walk(root_path: str, gitignored, prefix: str = '') -> list[str]:
    """Recursive tree printer. Applies EXCLUDE_DIRS, EXCLUDE_FILES, and .gitignore.

    gitignored is the callable returned by _gitignore_matcher. Directories and
    files that are gitignored are pruned from the display. The resource scanner
    (collect_resource_files) is kept deliberately separate — gitignored data
    files still appear in the map's Resources section because their schema is
    useful context even when their bulk is not in the repo.
    """
    out: list[str] = []
    try:
        entries = sorted(
            os.scandir(root_path),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except (PermissionError, OSError):
        return out

    entries = [
        e for e in entries
        if not (e.is_dir()  and (excluded_dir(e.name) or gitignored(e.path, True)))
        and not (e.is_file() and (e.name in EXCLUDE_FILES or gitignored(e.path, False)))
    ]

    for i, entry in enumerate(entries):
        is_last   = i == len(entries) - 1
        connector = '└── ' if is_last else '├── '
        extension = '    ' if is_last else '│   '
        out.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            out.extend(_tree_walk(entry.path, gitignored, prefix + extension))

    return out


def generate_tree(root: str) -> str:
    """Return a tree(1)-style listing of the project rooted at ``root``.

    Reads root/.gitignore (if present) and uses it to prune the display.
    Also applies EXCLUDE_DIRS / EXCLUDE_FILES from module scope so the tree
    is consistent with what the snapshot actually concatenates.
    No subprocess or external dependency.
    """
    abs_root   = os.path.abspath(root)
    patterns   = _load_gitignore(abs_root)
    gitignored = _gitignore_matcher(patterns, abs_root)

    lines = [abs_root]
    lines.extend(_tree_walk(abs_root, gitignored))
    return '\n'.join(lines)


# ─── Map mode: AST-based structural extraction ───────────────────────────────


def collect_python_files(root: str, extra_exclude: set) -> list[str]:
    """Return sorted list of .py file paths under root, applying exclude rules."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not excluded_dir(d) and d not in extra_exclude
        )
        for filename in sorted(filenames):
            if not filename.endswith('.py') or filename in EXCLUDE_FILES:
                continue
            full_path = os.path.join(dirpath, filename)
            rel_path  = os.path.relpath(full_path, root)
            if dir_rejects_file(rel_path):
                continue
            paths.append(full_path)
    return paths


def extract_docstring_body(docstring: str | None) -> str:
    """Return the docstring text with the surrounding === ruler lines stripped."""
    if not docstring:
        return ''
    lines = [
        ln for ln in docstring.splitlines()
        if not re.match(r'^\s*=+\s*$', ln)
    ]
    return '\n'.join(lines).strip()


def signature_for(node: ast.AST, source: str) -> str:
    """Return the verbatim signature line(s) for a class or function from source."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ''

    try:
        src_lines = source.splitlines()
        start = node.lineno - 1
        end   = (node.body[0].lineno - 1) if node.body else (start + 1)

        sig_lines = src_lines[start:end]
        if sig_lines:
            last = sig_lines[-1]
            comment_idx = last.find('  #')
            if comment_idx >= 0:
                last = last[:comment_idx].rstrip()
            colon_idx = last.rfind(':')
            if colon_idx >= 0:
                sig_lines[-1] = last[:colon_idx + 1]
            else:
                sig_lines[-1] = last

        return '\n'.join(sig_lines)
    except (AttributeError, IndexError):
        return ''


def is_interesting_method(name: str) -> bool:
    """Filter for which dunder methods to include in the map."""
    if not name.startswith('_'):
        return True
    if name == '__init__':
        return True
    if name.startswith('__') and name.endswith('__'):
        return False
    return True


def extract_top_level_constants(tree: ast.Module) -> list[str]:
    """Return UPPER_CASE module-level assignments rendered as 'NAME = ...'."""
    constants = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if not name.isupper() or len(name) < 2:
                continue
            try:
                rhs = ast.unparse(node.value)
            except (AttributeError, TypeError):
                rhs = '...'
            if len(rhs) > 80:
                rhs = rhs[:77] + '...'
            constants.append(f"{name} = {rhs}")
    return constants


def extract_declarations(source: str) -> tuple[list[str], list[str]]:
    """AST-walk a Python source file, return (signatures, constants)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ([f"# SYNTAX ERROR: {exc}"], [])

    signatures: list[str] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = signature_for(node, source)
            if sig:
                signatures.append(sig)
        elif isinstance(node, ast.ClassDef):
            sig = signature_for(node, source)
            if sig:
                signatures.append(sig)
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not is_interesting_method(member.name):
                    continue
                method_sig = signature_for(member, source)
                if method_sig:
                    signatures.append(method_sig)

    constants = extract_top_level_constants(tree)
    return signatures, constants


# ─── Map mode: resource summaries ────────────────────────────────────────────


def summarise_resource(path: str) -> str:
    """Return a one-line size/lines summary for a resource file."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return '_unreadable_'

    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            line_count = sum(1 for _ in f)
    except OSError:
        line_count = 0

    return f"{size:,} bytes · {line_count:,} lines"


def collect_resource_files(root: str) -> list[tuple[str, str]]:
    """Return [(path, summary), ...] for every resource file under MAP_INCLUDE_DIRS.

    Does NOT apply .gitignore — large data files and external corpora are often
    gitignored because of their size or origin, but their schema is still useful
    context in the map.
    """
    results: list[tuple[str, str]] = []
    for sub in MAP_INCLUDE_DIRS:
        full_dir = os.path.join(root, sub)
        if not os.path.isdir(full_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(full_dir):
            dirnames[:] = sorted(d for d in dirnames if not excluded_dir(d))
            for fname in sorted(filenames):
                if not fname.endswith(MAP_RESOURCE_EXTS):
                    continue
                full = os.path.join(dirpath, fname)
                results.append((full, summarise_resource(full)))
    return results


# ─── Map mode: rendering ─────────────────────────────────────────────────────


def _module_group(rel_path: str) -> str:
    """Pick the grouping key for the project map — the file's containing directory."""
    return os.path.dirname(rel_path) or '.'


def render_file_section(path: str) -> list[str]:
    """Render one file's map section: docstring body + declarations skeleton."""
    fname = os.path.basename(path)

    try:
        with open(path, 'r', encoding='utf-8') as fp:
            source = fp.read()
    except OSError as exc:
        return [f"### {fname}", f"_could not read: {exc}_", ""]

    try:
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
    except SyntaxError:
        docstring = None

    docstring_body = extract_docstring_body(docstring)
    signatures, constants = extract_declarations(source)

    out: list[str] = [f"### {fname}", '']

    if docstring_body:
        out.append('```')
        out.append(docstring_body)
        out.append('```')
        out.append('')

    if signatures or constants:
        out.append("```python")
        for sig in signatures:
            out.append(sig)
        if signatures and constants:
            out.append('')
        for const in constants:
            out.append(const)
        out.append("```")
        out.append('')

    return out


def render_map(root: str, extra_exclude: set) -> str:
    """Build the structured Markdown map covering Python files + resource summaries."""
    paths     = collect_python_files(root, extra_exclude)
    resources = collect_resource_files(root)

    ts      = datetime.now().strftime('%Y-%m-%d %H:%M')
    project = os.path.basename(os.path.abspath(root)) or 'project'

    out: list[str] = [
        f"# {project} — Python Project Map",
        f"_Generated {ts} · {len(paths)} Python files · {len(resources)} resources_",
        '',
    ]

    # Project tree — present in both snapshot and map mode.
    tree_text = generate_tree(root)
    if tree_text:
        out.append("## Project Tree")
        out.append('')
        out.append('```')
        out.append(tree_text.rstrip())
        out.append('```')
        out.append('')

    groups: dict[str, list[str]] = {}
    for path in paths:
        rel = os.path.relpath(path, root)
        groups.setdefault(_module_group(rel), []).append(path)

    for group in sorted(groups):
        out.append("---")
        out.append('')
        out.append(f"## {group}/")
        out.append('')
        for path in groups[group]:
            out.extend(render_file_section(path))

    if resources:
        out.append("---")
        out.append('')
        out.append("## Resources")
        out.append('')
        res_groups: dict[str, list[tuple[str, str]]] = {}
        for path, summary in resources:
            rel_dir = os.path.relpath(os.path.dirname(path), root)
            res_groups.setdefault(rel_dir, []).append((path, summary))

        for rel_dir in sorted(res_groups):
            out.append(f"### {rel_dir}/")
            out.append('')
            for path, summary in res_groups[rel_dir]:
                fname = os.path.basename(path)
                out.append(f"**{fname}** — {summary}")
                out.append('')

    return '\n'.join(out)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    """Parse args and dispatch to either snapshot or map mode."""
    parser = argparse.ArgumentParser(
        description="Concatenate (default) or map a Python codebase."
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
    parser.add_argument(
        '--map', action='store_true',
        help="Emit structured project map instead of full snapshot"
    )
    args = parser.parse_args()

    extra    = set(args.exclude or [])
    abs_root = os.path.abspath(args.root)

    if args.map:
        text = render_map(abs_root, extra)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as fp:
                fp.write(text)
            print(f"Written map to {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(text)
        return

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as fp:
            run_snapshot(abs_root, extra, fp)
        print(f"Written snapshot to {args.output}", file=sys.stderr)
    else:
        run_snapshot(abs_root, extra, sys.stdout)


if __name__ == '__main__':
    main()
