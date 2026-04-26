#!/bin/bash
# scripts/snap.sh — generate per-directory + whole-project tarzan snapshots
# and structured maps into html/. Run from the project root.

DIRS="core data docs eval prompts providers scripts setup tests utils"
for d in $DIRS; do
    scripts/tarzan.py --root "$d" > "html/$d.md"
    scripts/tarzan.py --map --root "$d" > "html/$d.map"
done
scripts/tarzan.py --root . --exclude $DIRS > html/root.md
scripts/tarzan.py --root . --exclude $DIRS --map > html/root.map
scripts/tarzan.py --root . > html/full.md
scripts/tarzan.py --root . --map > html/full.map
