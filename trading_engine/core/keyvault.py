#!/usr/bin/env python3
"""
Local KeyVault Engine for Trading Assistant
============================================
A secure, zero-cloud, hardware-aligned KeyVault mechanism for managing sensitive
credentials (broker API keys, Telegram tokens, DB passwords) completely outside
the Git repository.

Storage & Architecture:
- Vault Directory: ~/.trading_assistant/ (chmod 0700)
- Vault Ciphertext: ~/.trading_assistant/vault.enc (AES-128/256 Fernet authenticated encryption, chmod 0600)
- Master Key: ~/.trading_assistant/.vault_key (chmod 0600) or TRADING_VAULT_KEY env var
- Optional macOS Keychain integration via /usr/bin/security (service: "TradingAssistant")

Resolution Priority:
1. Local Encrypted KeyVault (~/.trading_assistant/vault.enc / OS Keychain)
2. Process Environment Variables (os.environ)
3. Local .env file (if present, for backward compatibility)
4. Default fallback value
"""

import os
import sys
import json
import stat
import subprocess
import logging
from pathlib import Path
from typing import Dict, Optional, List

logger = logging.getLogger("trading_engine.keyvault")

# Default vault directory outside the workspace
VAULT_DIR = Path(os.path.expanduser("~/.trading_assistant"))
VAULT_FILE = VAULT_DIR / "vault.enc"
KEY_FILE = VAULT_DIR / ".vault_key"
KEYCHAIN_SERVICE = "TradingAssistant"


class LocalKeyVault:
    """Manages encrypted local credentials securely outside the repository."""

    def __init__(self, vault_dir: Optional[Path] = None, use_keychain: bool = True):
        self.vault_dir = vault_dir or VAULT_DIR
        self.vault_file = self.vault_dir / "vault.enc"
        self.key_file = self.vault_dir / ".vault_key"
        self.use_keychain = use_keychain and sys.platform == "darwin"
        self._cipher = None
        self._ensure_storage_security()

    def _ensure_storage_security(self):
        """Ensures the vault directory and key files have strict 0700/0600 permissions."""
        if not self.vault_dir.exists():
            self.vault_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        else:
            try:
                os.chmod(self.vault_dir, stat.S_IRWXU)  # 0700
            except Exception:
                pass

    def _get_cipher(self):
        """Initializes or retrieves the Fernet cipher using the secure local master key."""
        if self._cipher is not None:
            return self._cipher

        from cryptography.fernet import Fernet

        key = os.environ.get("TRADING_VAULT_KEY")
        if not key:
            if self.key_file.exists():
                try:
                    with open(self.key_file, "rb") as f:
                        key = f.read().strip()
                except Exception as e:
                    logger.error(f"Failed reading vault key file: {e}")

            if not key:
                # Generate a new random 256-bit Fernet key
                key = Fernet.generate_key()
                with open(self.key_file, "wb") as f:
                    f.write(key)
                try:
                    os.chmod(self.key_file, stat.S_IRUSR | stat.S_IWUSR)  # 0600
                except Exception:
                    pass

        self._cipher = Fernet(key)
        return self._cipher

    def _load_vault_data(self) -> Dict[str, str]:
        """Loads and decrypts the encrypted local vault file."""
        if not self.vault_file.exists():
            return {}

        try:
            cipher = self._get_cipher()
            with open(self.vault_file, "rb") as f:
                encrypted_bytes = f.read()
            if not encrypted_bytes:
                return {}
            decrypted = cipher.decrypt(encrypted_bytes).decode("utf-8")
            return json.loads(decrypted)
        except Exception as e:
            logger.warning(f"Could not decrypt vault file (might be corrupted or key changed): {e}")
            return {}

    def _save_vault_data(self, data: Dict[str, str]):
        """Encrypts and atomically writes the vault dictionary to disk."""
        cipher = self._get_cipher()
        serialized = json.dumps(data, indent=2).encode("utf-8")
        encrypted = cipher.encrypt(serialized)

        temp_file = self.vault_dir / f".vault.tmp.{os.getpid()}"
        with open(temp_file, "wb") as f:
            f.write(encrypted)
        try:
            os.chmod(temp_file, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass

        temp_file.replace(self.vault_file)
        try:
            os.chmod(self.vault_file, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass

    # ── macOS Keychain Integration (Optional Hardware Enclave) ────────────────

    def _keychain_set(self, key: str, value: str) -> bool:
        if not self.use_keychain:
            return False
        try:
            subprocess.run(
                ["/usr/bin/security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key, "-w", value, "-U"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def _keychain_get(self, key: str) -> Optional[str]:
        if not self.use_keychain:
            return None
        try:
            res = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key, "-w"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            val = res.stdout.strip()
            return val if val else None
        except Exception:
            return None

    def _keychain_delete(self, key: str) -> bool:
        if not self.use_keychain:
            return False
        try:
            subprocess.run(
                ["/usr/bin/security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    # ── Core KeyVault API ─────────────────────────────────────────────────────

    def set_secret(self, key: str, value: str):
        """Sets a secret in the encrypted local KeyVault (and OS Keychain)."""
        clean_key = key.strip().upper()
        clean_val = str(value).strip()

        # 1. Save to encrypted file vault
        data = self._load_vault_data()
        data[clean_key] = clean_val
        self._save_vault_data(data)

        # 2. Sync to macOS Keychain if available
        self._keychain_set(clean_key, clean_val)
        return True

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Retrieves a secret with secure multi-layer resolution:
        1. Encrypted Local Vault
        2. OS Keychain (macOS)
        3. Environment Variables (os.environ)
        4. .env file
        5. Default fallback
        """
        clean_key = key.strip().upper()

        # 1. Check encrypted local vault
        data = self._load_vault_data()
        if clean_key in data and data[clean_key]:
            return data[clean_key]

        # 2. Check macOS Keychain
        kc_val = self._keychain_get(clean_key)
        if kc_val:
            return kc_val

        # 3. Check process environment variables
        env_val = os.environ.get(clean_key)
        if env_val:
            return env_val

        # 4. Check local .env as fallback if present
        try:
            from dotenv import dotenv_values
            dotenv_dict = dotenv_values(".env")
            if clean_key in dotenv_dict and dotenv_dict[clean_key]:
                return dotenv_dict[clean_key]
        except Exception:
            pass

        return default

    def delete_secret(self, key: str) -> bool:
        """Deletes a secret from KeyVault and Keychain."""
        clean_key = key.strip().upper()
        data = self._load_vault_data()
        deleted = False

        if clean_key in data:
            del data[clean_key]
            self._save_vault_data(data)
            deleted = True

        kc_deleted = self._keychain_delete(clean_key)
        return deleted or kc_deleted

    def list_secrets(self) -> Dict[str, str]:
        """Returns dictionary of secret names with masked values for security inspection."""
        data = self._load_vault_data()
        masked = {}
        for k, v in sorted(data.items()):
            if len(v) <= 6:
                masked[k] = "******"
            else:
                masked[k] = f"{v[:4]}****{v[-4:]}"
        return masked

    def import_from_env(self, env_path: str = ".env", wipe_source: bool = False) -> int:
        """Imports all variables from a local .env file into the KeyVault."""
        path = Path(env_path)
        if not path.exists():
            print(f"Error: {env_path} does not exist.")
            return 0

        imported_count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().upper()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    self.set_secret(k, v)
                    imported_count += 1

        print(f"✅ Successfully imported {imported_count} secret(s) from {env_path} into Local KeyVault.")

        if wipe_source:
            # Overwrite source with dummy template
            self.generate_env_example(example_path=env_path)
            print(f"🛡️ Sanitized {env_path} to prevent plaintext leaks.")

        return imported_count

    def generate_env_example(self, example_path: str = ".env.example"):
        """Generates a sanitized .env.example with dummy placeholders."""
        template = """# ─────────────────────────────────────────────────────────────────────────────
# Trading Assistant Environment Template
# NOTE: DO NOT store real credentials in this file.
# Store real credentials securely in your local KeyVault using:
#   python -m trading_engine.core.keyvault set KEY VALUE
# ─────────────────────────────────────────────────────────────────────────────

# Telegram Notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ENABLED=false

# Broker API (Zerodha Kite Connect)
KITE_API_KEY=
KITE_ACCESS_TOKEN=

# Database Connection
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading

# Web Server & Tunnel
PORT=8080
AUTO_START_TUNNEL=false
"""
        with open(example_path, "w", encoding="utf-8") as f:
            f.write(template)
        print(f"📄 Created clean template: {example_path}")


# ── Global KeyVault Singleton & Convenience Helpers ──────────────────────────

keyvault = LocalKeyVault()

def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve secret from KeyVault / Keychain / Environment."""
    return keyvault.get_secret(key, default)

def set_secret(key: str, value: str):
    """Store secret securely in KeyVault / Keychain."""
    return keyvault.set_secret(key, value)


# ── CLI Interface ─────────────────────────────────────────────────────────────

def _cli():
    import argparse

    parser = argparse.ArgumentParser(
        description="Local KeyVault Manager for Trading Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m trading_engine.core.keyvault set TELEGRAM_BOT_TOKEN 123456:ABC-DEF
  python -m trading_engine.core.keyvault get TELEGRAM_BOT_TOKEN
  python -m trading_engine.core.keyvault list
  python -m trading_engine.core.keyvault import-env
  python -m trading_engine.core.keyvault sanitize-env
""",
    )
    sub = parser.add_subparsers(dest="command")

    # set
    p_set = sub.add_parser("set", help="Store a secret in the local vault")
    p_set.add_argument("key", help="Secret key name (e.g. TELEGRAM_BOT_TOKEN)")
    p_set.add_argument("value", help="Secret value")

    # get
    p_get = sub.add_parser("get", help="Retrieve a secret from the vault")
    p_get.add_argument("key", help="Secret key name")

    # delete
    p_del = sub.add_parser("delete", help="Delete a secret from the vault")
    p_del.add_argument("key", help="Secret key name")

    # list
    sub.add_parser("list", help="List all stored secret keys (masked values)")

    # import-env
    p_imp = sub.add_parser("import-env", help="Import secrets from .env into vault")
    p_imp.add_argument("--file", default=".env", help="Path to .env file (default: .env)")
    p_imp.add_argument("--wipe", action="store_true", help="Sanitize source .env file after import")

    # sanitize-env
    p_san = sub.add_parser("sanitize-env", help="Replace .env with clean dummy template")
    p_san.add_argument("--file", default=".env", help="Path to .env file")

    # status
    sub.add_parser("status", help="Show KeyVault location and security status")

    args = parser.parse_args()

    kv = LocalKeyVault()

    if args.command == "set":
        kv.set_secret(args.key, args.value)
        print(f"🔒 Secret '{args.key.upper()}' stored securely in Local KeyVault.")

    elif args.command == "get":
        val = kv.get_secret(args.key)
        if val is not None:
            print(val)
        else:
            print(f"Key '{args.key.upper()}' not found in KeyVault or environment.", file=sys.stderr)
            sys.exit(1)

    elif args.command == "delete":
        if kv.delete_secret(args.key):
            print(f"🗑️ Secret '{args.key.upper()}' deleted from KeyVault.")
        else:
            print(f"Key '{args.key.upper()}' was not in KeyVault.")

    elif args.command == "list":
        secrets = kv.list_secrets()
        if not secrets:
            print("KeyVault is currently empty.")
        else:
            print("🔑 Stored KeyVault Secrets:")
            for k, v in secrets.items():
                print(f"  • {k:24} = {v}")

    elif args.command == "import-env":
        kv.import_from_env(env_path=args.file, wipe_source=args.wipe)

    elif args.command == "sanitize-env":
        kv.generate_env_example(example_path=args.file)

    elif args.command == "status":
        print("🛡️ Local KeyVault Status:")
        print(f"  • Vault Directory : {kv.vault_dir} (Exists: {kv.vault_dir.exists()})")
        print(f"  • Vault Ciphertext: {kv.vault_file} (Exists: {kv.vault_file.exists()})")
        print(f"  • Master Key File : {kv.key_file} (Exists: {kv.key_file.exists()})")
        print(f"  • macOS Keychain  : {'Enabled' if kv.use_keychain else 'Disabled/Unavailable'}")
        count = len(kv._load_vault_data())
        print(f"  • Stored Secrets  : {count}")

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
