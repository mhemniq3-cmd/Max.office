#!/usr/bin/env bash
# ==============================================================================
# Max Pro Cloud Licensing Server - Ubuntu / Debian VPS Deployment Script
# ==============================================================================
set -e

echo "🚀 Starting Max Pro License Server setup..."

# 1. Update system packages
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx

# 2. Setup project folder
APP_DIR="/var/www/maxpro-license"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER":"$USER" "$APP_DIR"

# 3. Create virtual environment
python3 -m venv "$APP_DIR/venv"
source "$APP_DIR/venv/bin/activate"

# 4. Install dependencies
pip install --upgrade pip
pip install fastapi uvicorn[standard] cryptography pydantic

echo "✅ Dependencies installed."

# 5. Create systemd service
SERVICE_FILE="/etc/systemd/system/maxpro-license.service"
sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Max Pro Cloud Licensing Server
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
Environment="PORT=8000"
Environment="HOST=127.0.0.1"
Environment="ADMIN_USERNAME=admin"
Environment="ADMIN_PASSWORD=ChangeThisPassword2026!"
ExecStart=$APP_DIR/venv/bin/uvicorn cloud_server.app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable maxpro-license
sudo systemctl restart maxpro-license

echo "✅ Systemd service installed and active."
echo "🎉 Server is running locally on port 8000."
