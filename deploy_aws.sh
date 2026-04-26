#!/bin/bash
set -e
echo "Starting AWS EC2 Deployment Setup for SILE CPD App..."

sudo apt update -y
sudo apt install -y python3 python3-pip python3-venv git

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
  sudo apt install -y nodejs
fi

if ! command -v pm2 >/dev/null 2>&1; then
  sudo npm install pm2@latest -g
fi

if [ ! -d venv ]; then
  python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt

python -m playwright install chromium
python -m playwright install-deps chromium

sudo ufw allow 80/tcp || true
sudo ufw allow 8501/tcp || true
sudo ufw allow 5000/tcp || true

pm2 startOrReload ecosystem.config.js
pm2 save
pm2 startup systemd -u "$USER" --hp "$HOME" | tail -n 1 | sudo bash || true

echo "Done. pm2 list:"
pm2 list
