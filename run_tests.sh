#!/bin/bash
# run_tests.sh
# Runs the full LLM layer test suite.
# Run from your Flask project root.

set -e

echo "🧪 Running LLM layer tests..."
echo ""

# Ensure we're running from the project root
if [ ! -d "core/llm" ]; then
    echo "❌ Must be run from the project root (core/llm not found)"
    exit 1
fi

# Activate virtualenv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Run each test module individually for clear output,
# then summarise with the combined runner

echo "── errors ──────────────────────────────────────────"
python -m pytest test/test_errors.py -v

echo ""
echo "── config ──────────────────────────────────────────"
python -m pytest test/test_config.py -v

echo ""
echo "── filters ─────────────────────────────────────────"
python -m pytest test/test_filters.py -v

echo ""
echo "── router ──────────────────────────────────────────"
python -m pytest test/test_router.py -v

echo ""
echo "── full suite ──────────────────────────────────────"
python -m pytest test/ -v --tb=short --asyncio-mode=auto

echo ""
echo "✅ Done."
