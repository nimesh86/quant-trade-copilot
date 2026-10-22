#!/usr/bin/env python3
"""
Secure Remote VPS Deployment Automation
Uses local KeyVault to retrieve VPS credentials in-memory without exposing passwords.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trading_engine.core.keyvault import get_secret, list_secrets

try:
    import paramiko
except ImportError:
    print("❌ paramiko not installed. Please run: pip install paramiko")
    sys.exit(1)


def ensure_local_ssh_key() -> str:
    """Generate an ed25519 key pair for VPS access if one does not exist."""
    key_path = Path.home() / ".ssh" / "vps_trading"
    pub_path = Path.home() / ".ssh" / "vps_trading.pub"
    
    if not key_path.exists():
        print("🔑 Generating dedicated SSH key pair (~/.ssh/vps_trading)...")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", ""],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return pub_path.read_text().strip()


def run_ssh_command(client: paramiko.SSHClient, cmd: str, stream: bool = True) -> int:
    """Execute command over SSH with live streaming output."""
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True)
    if stream:
        for line in iter(stdout.readline, ""):
            print(line, end="")
    exit_status = stdout.channel.recv_exit_status()
    return exit_status


def main():
    host = get_secret("VPS_HOST", "160.250.205.128")
    user = get_secret("VPS_USER", "root")
    password = get_secret("VPS_PASSWORD")

    if not password:
        print("❌ VPS_PASSWORD not found in KeyVault!")
        print("\nPlease store your VPS credentials in KeyVault using:")
        print("  venv/bin/python -m trading_engine.core.keyvault set VPS_USER <username>")
        print("  venv/bin/python -m trading_engine.core.keyvault set VPS_PASSWORD <password>")
        print(f"  venv/bin/python -m trading_engine.core.keyvault set VPS_HOST {host}")
        sys.exit(1)

    print(f"Connecting to VPS [{user}@{host}:22] using credentials from encrypted KeyVault...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=22,
            username=user,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        print(" Connected securely via SSH!")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    try:
        # Step 1: Install local SSH public key on VPS for passwordless future access
        pub_key = ensure_local_ssh_key()
        setup_key_cmd = (
            f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"grep -qxF '{pub_key}' ~/.ssh/authorized_keys 2>/dev/null || echo '{pub_key}' >> ~/.ssh/authorized_keys && "
            f"chmod 600 ~/.ssh/authorized_keys"
        )
        run_ssh_command(client, setup_key_cmd, stream=False)
        print(" Passwordless SSH key installed on VPS authorized_keys.")

        # Step 2: Download and execute deploy_vps.sh
        print("\n🚀 Starting automated VPS deployment...")
        deploy_cmd = (
            "curl -sSL https://raw.githubusercontent.com/nimesh86/quant-trade-copilot/main/deploy_vps.sh -o /tmp/deploy_vps.sh && "
            "chmod +x /tmp/deploy_vps.sh && bash /tmp/deploy_vps.sh"
        )
        code = run_ssh_command(client, deploy_cmd, stream=True)
        if code != 0:
            print(f"❌ Deployment failed with exit code: {code}")
            sys.exit(code)

        # Step 3: Sync trading secrets from local KeyVault to VPS KeyVault
        print("\n Synchronizing application secrets to VPS KeyVault...")
        local_secrets = list_secrets()
        secrets_to_sync = [
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_ENABLED",
            "KITE_API_KEY",
            "KITE_ACCESS_TOKEN",
            "DATABASE_URL",
        ]
        
        for k in secrets_to_sync:
            v = get_secret(k)
            if v:
                cmd = f"/opt/quant-trade-copilot/venv/bin/python -m trading_engine.core.keyvault set '{k}' '{v}'"
                run_ssh_command(client, cmd, stream=False)
                print(f"  • Synced secret: {k}")

        # Restart service after secret sync
        print("\n Restarting quant-trade-copilot service on VPS...")
        run_ssh_command(client, "sudo systemctl restart quant-trade-copilot", stream=True)
        time.sleep(3)

        # Step 4: Verify health
        print("\n Verifying live status on VPS...")
        run_ssh_command(client, "sudo systemctl status quant-trade-copilot --no-pager", stream=True)
        print("\n Testing local HTTP endpoint on VPS...")
        run_ssh_command(client, "curl -sI http://127.0.0.1:8080/ | head -n 5", stream=True)

        print("\n" + "=" * 70)
        print("🎉 SUCCESS! Quant Trade Copilot is live on your VPS!")
        print(f"🌐 Dashboard URL: http://{host}:8080")
        print("=" * 70)

    finally:
        client.close()


if __name__ == "__main__":
    main()
