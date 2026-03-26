#!/bin/bash
# run_tests.sh
# Runs the full test suite.
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

echo "── gtin ────────────────────────────────────────────"
python -m pytest tests/test_gtin.py -v

echo ""
echo "── errors ──────────────────────────────────────────"
python -m pytest tests/test_errors.py -v

echo ""
echo "── config ──────────────────────────────────────────"
python -m pytest tests/test_config.py -v

echo ""
echo "── filters ─────────────────────────────────────────"
python -m pytest tests/test_filters.py -v

echo ""
echo "── router ──────────────────────────────────────────"
python -m pytest tests/test_router.py -v

echo ""
echo "── full suite ──────────────────────────────────────"
python -m pytest tests/ -v --tb=short --asyncio-mode=auto

echo ""
echo "Done."
