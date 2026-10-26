"""
Cloudflare Tunnel Pipeline with Automatic Telegram Notifications.
──────────────────────────────────────────────────────────────────
Features:
1. Spawns and manages Cloudflare quick tunnel for local web engine.
2. Extracts and tracks public https://*.trycloudflare.com URL in real-time.
3. Automatically dispatches the public URL to Telegram on boot and whenever updated/changed.
4. Auto-heals: Restarts tunnel if interrupted and re-alerts Telegram with the new URL.
5. Persists state to trading_engine/web/tunnel_state.json for web dashboard integration.
"""
import os
import sys
import re
import json
import time
import signal
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

# Setup workspace paths
ENGINE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from adapters.telegram_notifier import default_telegram_notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tunnel_pipeline")

STATE_FILE = ENGINE_DIR / "web" / "tunnel_state.json"
URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def find_cloudflared_binary() -> Optional[str]:
    """Finds path to cloudflared executable."""
    candidates = [
        WORKSPACE_DIR / "venv" / "bin" / "cloudflared",
        Path("/usr/local/bin/cloudflared"),
        Path("/usr/bin/cloudflared"),
        Path("/tmp/cloudflared"),
    ]
    which_path = shutil.which("cloudflared")
    if which_path:
        candidates.append(Path(which_path))

    for p in candidates:
        if p and p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def save_tunnel_state(url: str, status: str = "active"):
    """Saves active tunnel metadata to JSON state file."""
    try:
        data = {
            "url": url,
            "status": status,
            "port": 8080,
            "updated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "telegram_configured": default_telegram_notifier.is_configured()
        }
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save tunnel state: {e}")


def get_stored_tunnel_url() -> Optional[str]:
    """Reads previously saved tunnel URL if exists."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("url")
        except Exception:
            return None
    return None


import threading

class TunnelPipeline:
    """Manages cloudflared lifecycle and Telegram broadcasts."""

    def __init__(self, target_port: int = 8080):
        self.target_port = target_port
        self._cleanup_orphaned_tunnels()
        self.binary_path = find_cloudflared_binary()
        self.current_url: Optional[str] = None
        self.notified_url: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None
        self.running = True
        # Watcher thread that automatically alerts Telegram as soon as TELEGRAM_BOT_TOKEN is saved
        threading.Thread(target=self._env_watcher_loop, daemon=True).start()

    def _cleanup_orphaned_tunnels(self):
        """Kills any previous cloudflared processes bound to this port."""
        try:
            res = subprocess.run(["pgrep", "-f", f"cloudflared.*localhost:{self.target_port}"], capture_output=True, text=True)
            for pid_str in res.stdout.strip().split():
                if pid_str.isdigit():
                    pid = int(pid_str)
                    if pid != os.getpid():
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            pass
        except Exception:
            pass

    def _env_watcher_loop(self):
        """Watches for TELEGRAM_BOT_TOKEN to be set in .env and delivers pending alerts."""
        while self.running:
            try:
                if self.current_url and self.notified_url != self.current_url:
                    if default_telegram_notifier.is_configured():
                        logger.info("Detected Telegram Bot Token configuration! Dispatching active URL alert...")
                        success = default_telegram_notifier.send_tunnel_url_alert(self.current_url, is_new=True)
                        if success:
                            self.notified_url = self.current_url
                            logger.info("Telegram notification sent successfully on config update! ✅")
            except Exception as e:
                logger.error(f"Error in env watcher loop: {e}")
            time.sleep(3)

    def stop(self):
        """Clean shutdown of tunnel process."""
        self.running = False
        if self.process and self.process.poll() is None:
            logger.info("Stopping Cloudflare tunnel process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
        save_tunnel_state("", status="stopped")

    def run_forever(self):
        """Runs the tunnel process in a self-healing supervision loop."""
        if not self.binary_path:
            logger.info("cloudflared binary not found; skipping background tunnel pipeline.")
            return

        logger.info(f"Starting Cloudflare Tunnel Pipeline on port {self.target_port}...")
        logger.info(f"Using cloudflared binary: {self.binary_path}")

        while self.running:
            cmd = [self.binary_path, "tunnel", "--no-prechecks", "--protocol", "http2", "--ha-connections", "1", "--url", f"http://localhost:{self.target_port}"]
            logger.info(f"Launching tunnel subprocess: {' '.join(cmd)}")

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                found_url = None
                for line in iter(self.process.stdout.readline, ""):
                    if not line:
                        break
                    line_clean = line.strip()

                    # Look for TryCloudflare URL in log output
                    match = URL_REGEX.search(line_clean)
                    if match:
                        url = match.group(0)
                        if url != found_url:
                            found_url = url
                            self._on_url_detected(url)

                    # Detect expired tunnel session error and force-restart to get a new tunnel
                    if "Unauthorized: Tunnel not found" in line_clean:
                        logger.warning("Detected expired quick tunnel session ('Unauthorized: Tunnel not found'). Terminating process to spawn a fresh tunnel...")
                        try:
                            self.process.terminate()
                        except Exception:
                            pass
                        break

                    # Also print info logs from cloudflared
                    if "INF" in line_clean or "ERR" in line_clean or "WRN" in line_clean:
                        if "metrics server" not in line_clean and "ICMP proxy" not in line_clean:
                            logger.info(f"[cloudflared] {line_clean}")

                self.process.stdout.close()
                rc = self.process.wait()
                logger.warning(f"Tunnel process terminated with code {rc}.")

            except Exception as e:
                logger.error(f"Tunnel process error: {e}")

            if self.running:
                logger.info("Reconnecting tunnel in 4 seconds...")
                time.sleep(4)

    def _on_url_detected(self, new_url: str):
        """Triggered whenever a new or changed tunnel URL is discovered."""
        is_update = bool(self.current_url and self.current_url != new_url)
        self.current_url = new_url
        logger.info("=" * 72)
        logger.info(f"🚀 PUBLIC CLOUDFLARE URL: {new_url}")
        logger.info("=" * 72)

        # Save to state file
        save_tunnel_state(new_url, status="active")

        # Dispatch alert to Telegram
        if default_telegram_notifier.is_configured():
            logger.info("Sending public URL notification to Telegram...")
            success = default_telegram_notifier.send_tunnel_url_alert(new_url, is_new=not is_update)
            if success:
                logger.info("Telegram notification sent successfully! ✅")
            else:
                logger.warning("Failed to send Telegram notification.")
        else:
            logger.warning(
                "Telegram bot token not yet configured in .env. "
                "URL saved to tunnel_state.json. Once TELEGRAM_BOT_TOKEN is set, "
                "subsequent updates will automatically ping your Telegram channel."
            )


def handle_signals(pipeline: TunnelPipeline):
    def _sig_handler(sig, frame):
        logger.info(f"Received signal {sig}, terminating pipeline...")
        pipeline.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI Trader Cloudflare Tunnel & Telegram Pipeline")
    parser.add_argument("--port", type=int, default=8080, help="Local port to forward (default: 8080)")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test message to Telegram bot")
    args = parser.parse_args()

    if args.test_telegram:
        print("\nTesting Telegram connection...")
        conn_res = default_telegram_notifier.test_connection()
        print("Bot Connection Check:", json.dumps(conn_res, indent=2))
        if default_telegram_notifier.is_configured():
            ok = default_telegram_notifier.send_message(
                "🧪 <b>AI Trader Bot Connected</b>\n\nTelegram notifications pipeline is active and ready to deliver Cloudflare URLs!"
            )
            print("Test Message Sent:", ok)
        else:
            print("Note: Set TELEGRAM_BOT_TOKEN in .env to enable dispatching.")
        sys.exit(0)

    pipeline = TunnelPipeline(target_port=args.port)
    handle_signals(pipeline)
    pipeline.run_forever()
