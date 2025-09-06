from os import getenv
from time import time
from dotenv import load_dotenv

# Attempt to load environment variables from a .env file named 'config.env'
try:
    load_dotenv("config.env")
except Exception as e:
    # If loading fails, continue silently or handle according to use case
    pass

# Validate essential environment variables after loading .env

# Check for BOT_TOKEN existence and format (should contain exactly one colon)
bot_token = getenv("BOT_TOKEN")
if not bot_token or bot_token.count(":") != 1:
    print("Error: BOT_TOKEN must be in format '123456:abcdefghijklmnopqrstuvwxyz'")
    exit(1)

# Check for a valid SESSION_STRING - must not be missing or a placeholder string
session_string = getenv("SESSION_STRING")
if not session_string or session_string == "xxxxxxxxxxxxxxxxxxxxxxx":
    print("Error: SESSION_STRING must be set with a valid string")
    exit(1)


# Configuration class for Pyrogram client settings
class PyroConf(object):
    # API_ID and API_HASH identify your Telegram application
    API_ID = int(getenv("API_ID", "6"))  # Default ID is 6 if not set, as Telegram's test app
    API_HASH = getenv("API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e")  # Default Telegram test API hash

    # Bot token for the Telegram bot, validated above
    BOT_TOKEN = bot_token

    # Session string for user authentication (string session), also validated above
    SESSION_STRING = session_string

    # Timestamp marking when the bot started (epoch time)
    BOT_START_TIME = time()
