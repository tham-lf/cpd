#!/bin/bash
set -e
echo "[update] Pulling latest from main..."
cd "$(dirname "$0")"
git fetch origin
git reset --hard origin/main

echo "[update] Activating venv and installing deps..."
source venv/bin/activate
pip install -q -r requirements.txt

echo "[update] Restarting PM2 services..."
pm2 startOrReload ecosystem.config.js
pm2 save

echo "[update] Done. Status:"
pm2 list
