import os
from pathlib import Path
from dotenv import load_dotenv

try:
    # Works with Pyrofork providing pyrogram API
    from pyrogram import Client
except Exception as e:
    print("Error: pyrogram/pyrofork is required. Install dependencies via 'pip install -r requirements.txt'.")
    raise


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.env"


def update_env_file(path: Path, key: str, value: str):
    lines = []
    found = False
    if path.exists():
        content = path.read_text(encoding="utf-8").splitlines()
        for line in content:
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(s: str, show: int = 6) -> str:
    if not s:
        return ""
    return s[:show] + "…" + s[-show:]


def main():
    print("== Telegram Session String Generator ==")
    print(f"Config file: {CONFIG_PATH}")

    if CONFIG_PATH.exists():
        load_dotenv(CONFIG_PATH)

    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")

    if not api_id or not api_hash:
        print("API_ID and/or API_HASH not found in config.env. Please enter them now.")
        api_id = input("API_ID: ").strip()
        api_hash = input("API_HASH: ").strip()
        update_env_file(CONFIG_PATH, "API_ID", api_id)
        update_env_file(CONFIG_PATH, "API_HASH", api_hash)

    try:
        api_id_int = int(api_id)  # validate
    except Exception:
        print("Error: API_ID must be an integer.")
        return

    print("\nA window of interactive prompts will follow:")
    print("- Enter your phone number with country code (e.g., +15551234567)")
    print("- Enter the login code sent by Telegram")
    print("- (If enabled) Enter your 2FA password\n")

    app = Client("gen_session", api_id=api_id_int, api_hash=api_hash)
    try:
        app.start()
        session_str = app.export_session_string()
        print("\nSession generated successfully!")
    finally:
        try:
            app.stop()
        except Exception:
            pass

    # Persist to config.env
    update_env_file(CONFIG_PATH, "SESSION_STRING", session_str)
    print(f"SESSION_STRING written to config.env: {mask(session_str)}")
    print("\nNext: run your bot normally, it will use the saved SESSION_STRING.")


if __name__ == "__main__":
    main()
