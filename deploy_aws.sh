#!/bin/bash
echo "Starting AWS EC2 Deployment Setup for SILE CPD App..."

sudo apt update -y
sudo apt install python3 -y
sudo apt install python3-pip -y
sudo apt install python3-venv -y

curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install pm2@latest -g

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python -m playwright install chromium
python -m playwright install-deps chromium

sudo ufw allow 80/tcp
sudo ufw allow 8501/tcp
sudo ufw allow 5000/tcp

echo "Done! You can now start the services using pm2 start ecosystem.config.js"
