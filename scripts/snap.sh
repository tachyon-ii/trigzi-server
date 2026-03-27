#!/bin/bash
#
#  scripts/snap.sh
#
#  Snapshot the codebase and publish to trigzi.com/trigzi_system.txt
#
#  Usage: ./scripts/snap.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR/.."
OUTPUT="$ROOT/html/trigzi_system.txt"

echo "Snapshotting codebase..."
python3 "$SCRIPT_DIR/tarzan.py" --output "$OUTPUT"

echo ""
echo "Published: https://trigzi.com/trigzi_system.txt"
