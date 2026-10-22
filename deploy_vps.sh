#!/usr/bin/env bash
set -e

# ==============================================================================
# Quant Trade Copilot - Automated VPS Deployment Script
# Target Host: 160.250.205.128
# ==============================================================================

REPO_URL="https://github.com/nimesh86/quant-trade-copilot.git"
APP_DIR="/opt/quant-trade-copilot"
SERVICE_NAME="quant-trade-copilot"

echo "🚀 [1/6] Updating system packages and installing prerequisites..."
sudo apt-get update -y
sudo apt-get install -y git python3 python3-venv python3-pip curl ufw build-essential libpq-dev

echo "📂 [2/6] Setting up application directory at $APP_DIR..."
if [ -d "$APP_DIR" ]; then
    echo "Directory exists. Pulling latest code..."
    cd "$APP_DIR"
    git pull origin main
else
    echo "Cloning repository..."
    sudo git clone "$REPO_URL" "$APP_DIR"
    sudo chown -R "$USER":"$USER" "$APP_DIR"
    cd "$APP_DIR"
fi

echo "🐍 [3/6] Configuring Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "⚙️ [4/6] Creating default environment template if missing..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

echo "🛡️ [5/6] Registering systemd service ($SERVICE_NAME)..."
sudo bash -c "cat <<EOF > /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=Quant Trade Copilot - Trading Engine & Web Dashboard
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/trading_engine/web/server.py
Restart=always
RestartSec=5
Environment=PORT=8080
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "🔥 [6/6] Configuring Firewall (UFW) for port 8080..."
if sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 8080/tcp comment "Quant Trade Copilot Dashboard"
    sudo ufw reload
fi

echo ""
echo "=============================================================================="
echo "✅ Deployment Complete!"
echo "Status check : sudo systemctl status $SERVICE_NAME"
echo "Live logs    : journalctl -u $SERVICE_NAME -f"
echo "Web URL      : http://160.250.205.128:8080"
echo ""
echo "🔑 Next Step: Configure your secrets into the VPS KeyVault:"
echo "   cd $APP_DIR"
echo "   venv/bin/python -m trading_engine.core.keyvault set TELEGRAM_BOT_TOKEN <value>"
echo "   venv/bin/python -m trading_engine.core.keyvault set TELEGRAM_CHAT_ID <value>"
echo "   venv/bin/python -m trading_engine.core.keyvault set KITE_API_KEY <value>"
echo "   venv/bin/python -m trading_engine.core.keyvault set KITE_ACCESS_TOKEN <value>"
echo "   sudo systemctl restart $SERVICE_NAME"
echo "=============================================================================="
