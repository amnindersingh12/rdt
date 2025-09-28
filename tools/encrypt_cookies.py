"""Utility to encrypt a raw cookies.txt for secure embedding via environment variables.

Workflow:
1. Place exported browser cookies into a file (Netscape format) e.g. cookies_plain.txt
2. Run: python tools/encrypt_cookies.py --input cookies_plain.txt
3. Output prints two values:
   FERNET_KEY=....
   ENCRYPTED_COOKIES=.... (base64 encoded cipher text)
4. Copy BOTH into your config (config.env / Heroku Config Vars / GitHub Actions secrets)
5. At runtime the application (main.py) will decrypt and write cookies/cookies.txt (gitignored)

Security Notes:
- The Fernet key MUST be kept secret. Anyone with key + ciphertext can recover cookies.
- Rotate periodically: re-run the script to produce a new key & cipher, update env vars, redeploy.
- Never commit decrypted cookies.txt.

Requires: cryptography
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from cryptography.fernet import Fernet


def read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def generate_key() -> bytes:
    return Fernet.generate_key()


def encrypt(data: bytes, key: bytes) -> str:
    f = Fernet(key)
    token = f.encrypt(data)
    # token is already urlsafe base64; still wrap in standard base64 for uniformity (optional)
    return base64.b64encode(token).decode()


def _update_config_env(key: str, enc: str, path: str = "config.env"):
    """Insert or replace FERNET_KEY / ENCRYPTED_COOKIES in config.env.
    Creates the file if missing. Safe best-effort; logs to stdout.
    """
    try:
        lines = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        def set_line(var, value):
            prefix = var + "="
            for i, l in enumerate(lines):
                if l.startswith(prefix):
                    lines[i] = f"{prefix}{value}\n"
                    return
            lines.append(f"{prefix}{value}\n")
        set_line("FERNET_KEY", key)
        set_line("ENCRYPTED_COOKIES", enc)
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        print(f"[+] Updated {path} with FERNET_KEY & ENCRYPTED_COOKIES")
    except Exception as e:
        print(f"[!] Failed to update {path}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Encrypt a cookies.txt file for env storage")
    parser.add_argument("--input", "-i", required=True, help="Path to raw cookies file (Netscape format)")
    parser.add_argument("--reuse-key", help="Existing Fernet key (optional) to reuse instead of generating new")
    parser.add_argument("--write-config", action="store_true", help="Also write/update FERNET_KEY & ENCRYPTED_COOKIES in config.env")
    args = parser.parse_args()

    raw = read_file(args.input)
    if not raw.strip():
        print("[!] Input file empty", file=sys.stderr)
        sys.exit(1)

    if args.reuse_key:
        key = args.reuse_key.encode()
    else:
        key = generate_key()

    encrypted_b64 = encrypt(raw, key)

    print("Add the following to your environment (do NOT commit raw cookies):\n")
    k_str = key.decode()
    print(f"FERNET_KEY={k_str}")
    print(f"ENCRYPTED_COOKIES={encrypted_b64}")
    if args.write_config:
        _update_config_env(k_str, encrypted_b64)
    print("\nVerification length:")
    print(f"  Raw bytes: {len(raw)} bytes")
    print(f"  Ciphertext (b64): {len(encrypted_b64)} chars")

    print("\nTo rotate: re-run without --reuse-key and replace both vars.")

if __name__ == "__main__":  # pragma: no cover
    main()
