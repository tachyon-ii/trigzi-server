#!/bin/bash
# run_tests.sh
# Runs the full test suite dynamically.
# Run from the project root.

set -e

ENV_FILE=/etc/trigzi/env
if [ -z "$DB_USER" ] && [ -f "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
fi

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

if [ ! -d "tests" ]; then
    echo "ERROR: Must be run from the project root (tests/ not found)"
    exit 1
fi

echo "Running test suite..."
echo ""

# 🛡️ THE FIX: Dynamically glob all test files for segmented output
for test_file in tests/test_*.py; do
    # Extract filename without path and .py extension
    filename=$(basename "$test_file" .py)
    # Strip the "test_" prefix for the clean header name
    module_name=${filename#test_}
    
    echo "── ${module_name} ────────────────────────────────────────"
    python -m pytest "$test_file" -v --asyncio-mode=auto
    echo ""
done

echo "── full suite ──────────────────────────────────────"
python -m pytest tests/ -v --tb=short --asyncio-mode=auto

echo ""
echo "Done."
