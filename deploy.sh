#!/bin/bash

# 1. The Choke Point
if [ -z "$VIRTUAL_ENV" ]; then
    echo "❌ ERROR: Virtual environment is not active!"
    echo "   Please run 'source venv/bin/activate' first."
    exit 1
fi

echo "✅ Virtual environment detected: $VIRTUAL_ENV"
echo "🚀 Bouncing Trigzi services..."

# 2. Restart the Flask/Gunicorn backend
sudo systemctl restart trigzi_api

# 3. Reload Nginx to pick up routing changes 
sudo systemctl reload nginx

echo "✅ Services restarted successfully."
sudo systemctl status trigzi_api --no-pager | grep Active
