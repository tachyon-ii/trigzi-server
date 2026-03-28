#!/bin/bash

ERRORS=0

# 1. Check environment is loaded
if [ -z "$DB_USER" ]; then
    echo "ERROR: Environment not loaded. Run: source /etc/trigzi/env"
    ERRORS=$((ERRORS + 1))
fi

# 2. Check virtualenv is active
if [ -z "$VIRTUAL_ENV" ]; then
    echo "ERROR: Virtual environment not active. Run: source venv/bin/activate"
    ERRORS=$((ERRORS + 1))
fi

if [ $ERRORS -gt 0 ]; then
    exit 1
fi

echo "OK: Environment $DB_USER@$DB_NAME"
echo "OK: Virtual environment $VIRTUAL_ENV"
echo "Bouncing Trigzi services..."

# first daemon-reload
sudo systemctl daemon-reload

# 3. Restart the Flask/Gunicorn backend
sudo systemctl restart trigzi_api

# 4. Reload Nginx to pick up routing changes
sudo systemctl reload nginx

echo "Done."
sudo systemctl status trigzi_api --no-pager | grep Active
