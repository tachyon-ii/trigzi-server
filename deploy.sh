#!/bin/bash

# 1. Check environment is loaded
if [ -z "$DB_USER" ]; then
    echo "ERROR: Environment not loaded."
    echo "Please run: source /etc/trigzi/env"
    exit 1
fi

# 2. Check virtualenv is active
if [ -z "$VIRTUAL_ENV" ]; then
    echo "ERROR: Virtual environment is not active."
    echo "Please run: source venv/bin/activate"
    exit 1
fi

echo "OK: Environment $DB_USER@$DB_NAME"
echo "OK: Virtual environment $VIRTUAL_ENV"
echo "Bouncing Trigzi services..."

# 3. Restart the Flask/Gunicorn backend
sudo systemctl restart trigzi_api

# 4. Reload Nginx to pick up routing changes
sudo systemctl reload nginx

echo "Done."
sudo systemctl status trigzi_api --no-pager | grep Active
