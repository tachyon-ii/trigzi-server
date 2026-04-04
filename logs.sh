#!/bin/bash

ACCESS_LOG="/var/log/nginx/trigzi.access.log"
ERROR_LOG="/var/log/nginx/trigzi.error.log"
API_LOG="/var/www/trigzi/logs/api.log"

if [ "$1" == "clear" ]; then
    sudo truncate -s 0 "$ACCESS_LOG"
    sudo truncate -s 0 "$ERROR_LOG"
    sudo truncate -s 0 "$API_LOG"
    echo "✅ Logs cleared."
    exit 0
fi

# Use argument 1, default to 10 if empty
NUM_LINES=${1:-10}

echo -e "\n===== trigzi.access.log ====="
sudo tail -n "$NUM_LINES" "$ACCESS_LOG"

echo -e "\n===== trigzi.error.log ====="
sudo tail -n "$NUM_LINES" "$ERROR_LOG"

echo -e "\n===== journalctl trigzi_api ====="
sudo journalctl -u trigzi_api -n "$NUM_LINES" --no-pager

echo -e "\n===== api.log ====="
sudo tail -n "$NUM_LINES" "$API_LOG"
