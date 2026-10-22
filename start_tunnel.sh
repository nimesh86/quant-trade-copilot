#!/usr/bin/env bash
# ── AI Trader Cloudflare Tunnel & Telegram Broadcast Pipeline ───────────────────
# Starts the Cloudflare quick tunnel, tracks public URL, and notifies Telegram.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "venv/bin/python" ]; then
    PYTHON_BIN="venv/bin/python"
else
    PYTHON_BIN="python3"
fi

echo "======================================================================"
echo " Starting AI Trader Cloudflare Tunnel & Telegram Pipeline"
echo "======================================================================"

exec $PYTHON_BIN trading_engine/tunnel_pipeline.py --port 8080 "$@"
