import os
import shutil
import psutil
import asyncio
from time import time

from pyleaves import Leaves
from pyrogram.enums import ParseMode
from pyrogram import Client, filters
from pyrogram.errors import PeerIdInvalid, BadRequest
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from helpers.utils import processMediaGroup, progressArgs, send_media
from helpers.files import get_download_path, fileSizeLimit, get_readable_file_size, get_readable_time, cleanup_download
from helpers.msg import getChatMsgID, get_file_name, get_parsed_msg
from helpers.channel import ChannelCloner
from helpers.forwarding import ForwardingManager, normalize_identifier
from helpers.mirroring import MirrorManager
from helpers.replication import ReplicationManager
from helpers.external import is_supported_url, extract_supported_url
from helpers.external_handler import handle_external

from helpers.config_store import (
    load_config,
    set_mirror_enabled,
    add_mirror_rule,
    remove_mirror_rule,
    clear_mirror_rules,
)

from config import PyroConf
from logger import LOGGER


# Startup environment checks
def _env_checks():
    import shutil
    ff = shutil.which("ffmpeg")
    if not ff:
        candidate_dirs = [
            "/app/.apt/usr/bin",
            "/usr/bin",
            "/usr/local/bin",
        ]
        found = None
        for d in candidate_dirs:
            cand = os.path.join(d, "ffmpeg")
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                found = cand
                break
        if found:
            dir_path = os.path.dirname(found)
            os.environ["PATH"] = dir_path + os.pathsep + os.environ.get("PATH", "")
            LOGGER(__name__).info(f"ffmpeg detected at {found} (added to PATH).")
        else:
            LOGGER(__name__).warning(
                "FFMPEG NOT FOUND - External downloads may lose audio or fail to merge streams."
            )
    else:
        LOGGER(__name__).info(f"ffmpeg detected at {ff}.")


_env_checks()


def _auto_encrypt_cookies():
    """Automatically encrypt a local raw cookies source if encryption vars absent."""
    if os.environ.get("AUTO_ENCRYPT_COOKIES", "true").lower() in ("false", "0", "no"):
        return
    if os.environ.get("FERNET_KEY") and os.environ.get("ENCRYPTED_COOKIES"):
        return
    candidates = [
        "cookies_raw.txt",
        os.path.join("cookies", "cookies_raw.txt"),
        os.path.join("cookies", "cookies.txt"),
    ]
    raw_path = None
    for c in candidates:
        if os.path.isfile(c):
            raw_path = c
            break
    if not raw_path:
        return
    try:
        with open(raw_path, "rb") as fh:
            raw_bytes = fh.read()
        if not raw_bytes.strip():
            return
        try:
            from cryptography.fernet import Fernet
        except Exception as e:
            LOGGER(__name__).warning(f"Cannot auto-encrypt cookies: cryptography missing: {e}")
            return
        import base64
        key = Fernet.generate_key()
        f = Fernet(key)
        token = f.encrypt(raw_bytes)
        enc_b64 = base64.b64encode(token).decode()
        os.environ["FERNET_KEY"] = key.decode()
        os.environ["ENCRYPTED_COOKIES"] = enc_b64
        try:
            os.makedirs("cookies", exist_ok=True)
            with open(os.path.join("cookies", "encrypted_cookies.b64"), "w", encoding="utf-8") as ef:
                ef.write(enc_b64)
            with open(os.path.join("cookies", "fernet.key"), "w", encoding="utf-8") as kf:
                kf.write(key.decode())
            LOGGER(__name__).info("Wrote cookies/encrypted_cookies.b64 and cookies/fernet.key.")
        except Exception as e:
            LOGGER(__name__).warning(f"Failed writing encrypted cookie artifacts: {e}")
        LOGGER(__name__).info(f"Auto-encrypted cookies from {raw_path} -> environment.")
        if os.path.isfile("config.env"):
            try:
                with open("config.env", "r", encoding="utf-8", errors="ignore") as fh:
                    lines = fh.readlines()
                def set_line(var, value):
                    prefix = var + "="
                    for i,l in enumerate(lines):
                        if l.startswith(prefix):
                            lines[i] = f"{prefix}{value}\n"
                            return
                    lines.append(f"{prefix}{value}\n")
                set_line("FERNET_KEY", key.decode())
                set_line("ENCRYPTED_COOKIES", enc_b64)
                with open("config.env", "w", encoding="utf-8") as fh:
                    fh.writelines(lines)
                LOGGER(__name__).info("config.env updated with auto-encrypted cookie vars.")
            except Exception as e:
                LOGGER(__name__).warning(f"Failed to write config.env with cookie vars: {e}")
        if os.environ.get("HEROKU_AUTO_SET_CONFIG", "false").lower() in ("true", "1", "yes"):
            app = os.environ.get("HEROKU_APP_NAME")
            api_key = os.environ.get("HEROKU_API_KEY")
            if app and api_key:
                try:
                    import requests
                    url = f"https://api.heroku.com/apps/{app}/config-vars"
                    headers = {
                        "Accept": "application/vnd.heroku+json; version=3",
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    resp = requests.patch(url, headers=headers, json={
                        "FERNET_KEY": key.decode(),
                        "ENCRYPTED_COOKIES": enc_b64,
                    }, timeout=15)
                    if resp.status_code in (200, 201):
                        LOGGER(__name__).info("Pushed encrypted cookies vars to Heroku app.")
                    else:
                        LOGGER(__name__).warning(f"Heroku config update failed: {resp.status_code}")
                except Exception as e:
                    LOGGER(__name__).warning(f"Could not push vars to Heroku: {e}")
    except Exception as e:
        LOGGER(__name__).warning(f"Auto cookie encryption error: {e}")


_auto_encrypt_cookies()


def _decrypt_cookies_if_present():
    """Decrypt encrypted cookies from environment if provided."""
    key = os.environ.get("FERNET_KEY")
    enc = os.environ.get("ENCRYPTED_COOKIES")
    if not key or not enc:
        return
    try:
        from cryptography.fernet import Fernet
    except Exception as e:
        LOGGER(__name__).warning(f"cryptography not installed, cannot decrypt cookies: {e}")
        return
    try:
        import base64
        token = base64.b64decode(enc)
        f = Fernet(key.encode())
        decrypted = f.decrypt(token)
        os.makedirs("cookies", exist_ok=True)
        dest = os.path.join("cookies", "cookies.txt")
        with open(dest, "wb") as fh:
            fh.write(decrypted)
        LOGGER(__name__).info("Decrypted cookies.txt from environment.")
    except Exception as e:
        LOGGER(__name__).error(f"Failed to decrypt cookies: {e}")


_decrypt_cookies_if_present()


# Initialize bot and user clients
bot = Client(
    "media_bot",
    api_id=PyroConf.API_ID,
    api_hash=PyroConf.API_HASH,
    bot_token=PyroConf.BOT_TOKEN,
    workers=1000,
    parse_mode=ParseMode.MARKDOWN,
)

user = Client(
    "user_session",
    workers=1000,
    session_string=PyroConf.SESSION_STRING,
)

# Initialize channel cloner
channel_cloner = ChannelCloner(user, bot, delay=1.0)
forwarding_manager = ForwardingManager(user)
mirror_manager = MirrorManager(user, channel_cloner)
replication_manager = ReplicationManager(user)

RUNNING_TASKS = set()


def _init_replication_mappings():
    """Initialize default replication mappings on startup."""
    # Your predefined channel mappings
    DEFAULT_MAPPINGS = [
        {"source": -1002416589505, "target": -1003672461179, "enabled": True},
        {"source": -1002523833295, "target": -1002581484854, "enabled": True},
    ]
    
    current = replication_manager.get_mappings()
    if not current:
        # Only set defaults if no mappings exist
        replication_manager.set_mappings(DEFAULT_MAPPINGS)
        LOGGER(__name__).info(f"Initialized {len(DEFAULT_MAPPINGS)} default replication mappings")


def track_task(coro):
    """Create and track an asyncio task."""
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    def _cleanup(_):
        RUNNING_TASKS.discard(task)
    task.add_done_callback(_cleanup)
    return task


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu with all major features as buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Downloads", callback_data="menu:downloads"),
            InlineKeyboardButton("📡 Cloning", callback_data="menu:cloning"),
        ],
        [
            InlineKeyboardButton("🔄 Forward", callback_data="menu:forward"),
            InlineKeyboardButton("🪞 Mirror", callback_data="menu:mirror"),
        ],
        [
            InlineKeyboardButton("📋 Replicate", callback_data="menu:replicate"),
            InlineKeyboardButton("🌐 External", callback_data="menu:external"),
        ],
        [
            InlineKeyboardButton("🛠️ Tools", callback_data="menu:tools"),
            InlineKeyboardButton("📊 Stats", callback_data="action:stats"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="menu:help"),
            InlineKeyboardButton("❌ Close", callback_data="menu:close"),
        ],
    ])


def _back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Simple back button to return to main menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _downloads_keyboard() -> InlineKeyboardMarkup:
    """Downloads section with action buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Single Post", callback_data="guide:dl"),
            InlineKeyboardButton("📦 Batch Download", callback_data="guide:bdl"),
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _cloning_keyboard() -> InlineKeyboardMarkup:
    """Cloning section with action buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Clone Channel", callback_data="guide:clone_channel"),
            InlineKeyboardButton("📊 Clone Range", callback_data="guide:clone_range"),
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _forward_keyboard() -> InlineKeyboardMarkup:
    """Forwarding section with action buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Enable", callback_data="fwd:enable"),
            InlineKeyboardButton("❌ Disable", callback_data="fwd:disable"),
        ],
        [
            InlineKeyboardButton("🎯 Set Target", callback_data="guide:fwd_target"),
            InlineKeyboardButton("➕ Add Source", callback_data="guide:fwd_addsrc"),
        ],
        [
            InlineKeyboardButton("➖ Remove Source", callback_data="guide:fwd_rmsrc"),
            InlineKeyboardButton("🗑️ Clear All", callback_data="fwd:clearsrc"),
        ],
        [
            InlineKeyboardButton("📋 View Status", callback_data="action:fwd_status"),
            InlineKeyboardButton("◀️ Back", callback_data="menu:main"),
        ],
    ])


def _mirror_keyboard() -> InlineKeyboardMarkup:
    """Mirror section with action buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Enable", callback_data="mir:enable"),
            InlineKeyboardButton("❌ Disable", callback_data="mir:disable"),
        ],
        [
            InlineKeyboardButton("➕ Add Rule", callback_data="guide:mir_add"),
            InlineKeyboardButton("➖ Remove Rule", callback_data="guide:mir_rm"),
        ],
        [
            InlineKeyboardButton("🗑️ Clear All", callback_data="mir:clear"),
            InlineKeyboardButton("📋 View Rules", callback_data="action:mir_status"),
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _replication_keyboard() -> InlineKeyboardMarkup:
    """Replication section with action buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Enable", callback_data="rep:enable"),
            InlineKeyboardButton("❌ Disable", callback_data="rep:disable"),
        ],
        [
            InlineKeyboardButton("🔄 Backfill All", callback_data="rep:backfill"),
            InlineKeyboardButton("📋 View Status", callback_data="action:rep_status"),
        ],
        [
            InlineKeyboardButton("🛑 Stop Backfills", callback_data="rep:stop"),
            InlineKeyboardButton("⚙️ Commands", callback_data="guide:replicate"),
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _external_keyboard() -> InlineKeyboardMarkup:
    """External downloads section."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 YouTube", callback_data="guide:ext_yt"),
            InlineKeyboardButton("📸 Instagram", callback_data="guide:ext_ig"),
        ],
        [
            InlineKeyboardButton("📌 Pinterest", callback_data="guide:ext_pin"),
            InlineKeyboardButton("🍪 Set Cookies", callback_data="guide:cookies"),
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _tools_keyboard() -> InlineKeyboardMarkup:
    """Tools section with action buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats", callback_data="action:stats"),
            InlineKeyboardButton("📋 Status", callback_data="action:status"),
        ],
        [
            InlineKeyboardButton("📄 Logs", callback_data="action:logs"),
            InlineKeyboardButton("🛑 Cancel All", callback_data="action:killall"),
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="menu:main")],
    ])


def _help_keyboard(active: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🏠 Home", callback_data="help:home"),
                InlineKeyboardButton("📥 Downloads", callback_data="help:downloads"),
                InlineKeyboardButton("📡 Cloning", callback_data="help:cloning"),
            ],
            [
                InlineKeyboardButton("🔄 Forward", callback_data="help:forward"),
                InlineKeyboardButton("🪞 Mirror", callback_data="help:mirror"),
                InlineKeyboardButton("🌐 External", callback_data="help:external"),
            ],
            [
                InlineKeyboardButton("🛠️ Tools", callback_data="help:tools"),
                InlineKeyboardButton("◀️ Menu", callback_data="menu:main"),
                InlineKeyboardButton("❌ Close", callback_data="help:close"),
            ],
        ]
    )


def _menu_text(section: str = "main") -> str:
    """Generate menu text for each section."""
    if section == "main":
        return (
            "🤖 **Media Bot Control Panel**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Welcome! Choose an option below to get started.\n\n"
            "**Quick Actions:**\n"
            "• Send any Telegram post URL to download\n"
            "• Send YouTube/Instagram/Pinterest URL for external download\n\n"
            "**Available Features:**\n"
            "📥 **Downloads** - Download from Telegram posts\n"
            "📡 **Cloning** - Clone channels/messages\n"
            "🔄 **Forward** - Auto-forward (many→one)\n"
            "🪞 **Mirror** - Mirror (one→many)\n"
            "🌐 **External** - YouTube, Instagram, Pinterest\n"
            "🛠️ **Tools** - Stats, logs, task management"
        )
    if section == "downloads":
        return (
            "📥 **Downloads Menu**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Download media from Telegram posts.\n\n"
            "**Options:**\n"
            "• **Single Post** - Download one post\n"
            "• **Batch Download** - Download a range of posts\n\n"
            "**Tip:** Just send a Telegram post URL directly!"
        )
    if section == "cloning":
        return (
            "📡 **Channel Cloning Menu**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Clone entire channels or specific message ranges.\n\n"
            "**Options:**\n"
            "• **Clone Channel** - Clone entire channel\n"
            "• **Clone Range** - Clone specific message IDs\n\n"
            "**Note:** Only forwards photos, videos, documents, stickers.\n"
            "Text and audio messages are skipped."
        )
    if section == "forward":
        return (
            "🔄 **Auto-Forward Menu** (Many → One)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Monitor multiple channels and forward to one destination.\n\n"
            "**Quick Setup:**\n"
            "1️⃣ Set target channel\n"
            "2️⃣ Add source channels\n"
            "3️⃣ Enable forwarding\n\n"
            "New posts from sources will auto-forward to target."
        )
    if section == "mirror":
        return (
            "🪞 **Mirroring Menu** (One → Many)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Mirror one source to multiple target channels.\n\n"
            "**Quick Setup:**\n"
            "1️⃣ Add mirror rules (source → targets)\n"
            "2️⃣ Enable mirroring\n\n"
            "New posts from source will be copied to all targets."
        )
    if section == "external":
        return (
            "🌐 **External Downloads Menu**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Download from external platforms.\n\n"
            "**Supported:**\n"
            "🎬 **YouTube** - Videos, shorts, music\n"
            "📸 **Instagram** - Reels, posts, stories\n"
            "📌 **Pinterest** - Pins, videos\n\n"
            "**Tip:** Just send the URL directly, or use `/ext <url>`\n\n"
            "**For best results:** Set up cookies to bypass restrictions."
        )
    if section == "tools":
        return (
            "🛠️ **Tools Menu**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "System utilities and task management.\n\n"
            "**Available:**\n"
            "📊 **Stats** - Bot statistics & system info\n"
            "📋 **Status** - View running tasks\n"
            "📄 **Logs** - Download log file\n"
            "🛑 **Cancel All** - Stop all running tasks"
        )
    if section == "replicate":
        return (
            "📋 **Channel Replication Menu**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Clone and sync channels with real-time monitoring.\n\n"
            "**Features:**\n"
            "✅ **Full message support** - Text, media, polls, PDFs, etc.\n"
            "🔄 **Reply preservation** - Maintains reply relationships\n"
            "📡 **Real-time sync** - Auto-clones new messages\n"
            "⚡ **Backfill** - Catch up on missed messages\n"
            "🔒 **Deduplication** - No duplicate cloning\n\n"
            "**Current Mappings:**\n"
            "`-1002416589505` → `-1003672461179`\n"
            "`-1002523833295` → `-1002581484854`"
        )
    return ""


def _guide_text(guide: str) -> str:
    """Generate instructional text for each feature."""
    guides = {
        "dl": (
            "📥 **Single Post Download**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/dl <post_url>`\n\n"
            "**Examples:**\n"
            "• `/dl https://t.me/channel/123`\n"
            "• `/dl https://t.me/c/1234567890/456`\n\n"
            "**Or just send the URL directly!**"
        ),
        "bdl": (
            "📦 **Batch Download**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/bdl <start_url> <end_url>`\n\n"
            "**Example:**\n"
            "`/bdl https://t.me/channel/100 https://t.me/channel/200`\n\n"
            "This downloads messages 100 through 200."
        ),
        "clone_channel": (
            "📡 **Clone Entire Channel**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/clone_channel <source> <target>`\n\n"
            "**Examples:**\n"
            "• `/clone_channel @source @target`\n"
            "• `/clone_channel -1001234567890 @mytarget`\n\n"
            "**Target formats:**\n"
            "• `@username` - Public channel/group\n"
            "• `https://t.me/+ABC123` - Private invite link\n"
            "• `-1001234567890` - Channel ID"
        ),
        "clone_range": (
            "📊 **Clone Message Range**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/clone_range <source> <target> <start_id> <end_id>`\n\n"
            "**Examples:**\n"
            "• `/clone_range @source @target 100 200`\n"
            "• `/clone_range -1001234567890 @mytarget 500 600`\n\n"
            "Range is inclusive (100-200 = 101 messages)."
        ),
        "fwd_target": (
            "🎯 **Set Forward Target**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/forward settarget <channel>`\n\n"
            "**Examples:**\n"
            "• `/forward settarget @mytarget`\n"
            "• `/forward settarget -1001234567890`"
        ),
        "fwd_addsrc": (
            "➕ **Add Source Channels**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/forward addsrc <ch1,ch2,...>`\n\n"
            "**Examples:**\n"
            "• `/forward addsrc @source1`\n"
            "• `/forward addsrc @src1,@src2,-1001234567890`"
        ),
        "fwd_rmsrc": (
            "➖ **Remove Source Channels**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/forward rmsrc <ch1,ch2,...>`\n\n"
            "**Examples:**\n"
            "• `/forward rmsrc @source1`\n"
            "• `/forward rmsrc @src1,@src2`"
        ),
        "mir_add": (
            "➕ **Add Mirror Rule**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/mirror add <source> <target1,target2,...>`\n\n"
            "**Examples:**\n"
            "• `/mirror add @source @target1`\n"
            "• `/mirror add @source @target1,@target2,-1001234567890`"
        ),
        "mir_rm": (
            "➖ **Remove Mirror Rule**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/mirror rm <source>` - Remove entire rule\n"
            "`/mirror rm <source> <target1,target2>` - Remove specific targets\n\n"
            "**Examples:**\n"
            "• `/mirror rm @source`\n"
            "• `/mirror rm @source @target1`"
        ),
        "ext_yt": (
            "🎬 **YouTube Download**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/ext <youtube_url>`\n\n"
            "**Examples:**\n"
            "• `/ext https://youtube.com/watch?v=xxx`\n"
            "• `/ext https://youtu.be/xxx`\n"
            "• `/ext https://youtube.com/shorts/xxx`\n\n"
            "**Or just send the URL directly!**\n\n"
            "**Tip:** Set cookies with `/cookies` to bypass age/login restrictions."
        ),
        "ext_ig": (
            "📸 **Instagram Download**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/ext <instagram_url>`\n\n"
            "**Examples:**\n"
            "• `/ext https://instagram.com/p/xxx`\n"
            "• `/ext https://instagram.com/reel/xxx`\n\n"
            "**Or just send the URL directly!**\n\n"
            "**Tip:** Set cookies with `/cookies` for private content."
        ),
        "ext_pin": (
            "📌 **Pinterest Download**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Usage:**\n"
            "`/ext <pinterest_url>`\n\n"
            "**Examples:**\n"
            "• `/ext https://pinterest.com/pin/xxx`\n"
            "• `/ext https://pin.it/xxx`\n\n"
            "**Or just send the URL directly!**"
        ),
        "cookies": (
            "🍪 **Set Cookies**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Cookies help bypass login/age restrictions.\n\n"
            "**Steps:**\n"
            "1️⃣ Export cookies from your browser\n"
            "   (Use extension like \"Get cookies.txt\")\n"
            "2️⃣ Send the `cookies.txt` file to this bot\n"
            "3️⃣ Reply to the file with `/cookies`\n\n"
            "**Done!** Cookies will be used for external downloads."
        ),
        "replicate": (
            "📋 **Replication Commands**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "`/replicate` - View status and mappings\n"
            "`/replicate enable` - Enable replication\n"
            "`/replicate disable` - Disable replication\n"
            "`/replicate add <source> <target>` - Add mapping\n"
            "`/replicate rm <source> <target>` - Remove mapping\n"
            "`/replicate backfill` - Backfill all mappings\n"
            "`/replicate backfill <source> <target>` - Backfill specific\n"
            "`/replicate stop` - Stop all backfills\n\n"
            "**How it works:**\n"
            "1️⃣ Backfill catches up on existing messages\n"
            "2️⃣ Real-time monitoring copies new messages\n"
            "3️⃣ Reply relationships are preserved\n"
            "4️⃣ All message types are supported"
        ),
    }
    return guides.get(guide, "Guide not found.")


def _help_text(section: str = "home") -> str:
    if section == "downloads":
        return (
            "📥 **Downloads Help**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "• `/dl <post_url>` - Download single post\n"
            "• `/bdl <start> <end>` - Batch download range\n\n"
            "**Auto-download:** Just send any Telegram post URL!\n\n"
            "**Supported:**\n"
            "• Photos, videos, audio, documents\n"
            "• Media groups (albums)\n"
            "• Protected/restricted channels"
        )
    if section == "cloning":
        return (
            "📡 **Cloning Help**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "• `/clone_channel <src> <tgt>` - Clone entire channel\n"
            "• `/clone_range <src> <tgt> <start> <end>` - Clone range\n"
            "• `/ui` - Interactive guided mode\n\n"
            "**Notes:**\n"
            "• Only forwards photos, videos, docs, stickers\n"
            "• Text/audio/captions are skipped\n"
            "• Works with protected channels"
        )
    if section == "forward":
        return (
            "🔄 **Auto-Forward Help** (Many → One)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "• `/forward` - View settings\n"
            "• `/forward enable|disable` - Toggle\n"
            "• `/forward settarget <ch>` - Set destination\n"
            "• `/forward addsrc <ch1,ch2>` - Add sources\n"
            "• `/forward rmsrc <ch1,ch2>` - Remove sources\n"
            "• `/forward clearsrc` - Clear all sources\n\n"
            "**Flow:** Sources → Single Target"
        )
    if section == "mirror":
        return (
            "🪞 **Mirroring Help** (One → Many)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "• `/mirror` - View settings & rules\n"
            "• `/mirror enable|disable` - Toggle\n"
            "• `/mirror add <src> <t1,t2>` - Add rule\n"
            "• `/mirror rm <src> [targets]` - Remove rule\n"
            "• `/mirror clear` - Clear all rules\n\n"
            "**Flow:** Single Source → Multiple Targets"
        )
    if section == "external":
        return (
            "🌐 **External Downloads Help**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "• `/ext <url>` - Download from URL\n"
            "• `/cookies` - Set up cookies (reply to file)\n\n"
            "**Supported Platforms:**\n"
            "• YouTube (videos, shorts, music)\n"
            "• Instagram (posts, reels, stories)\n"
            "• Pinterest (pins, videos)\n\n"
            "**Auto-detect:** Just send the URL directly!"
        )
    if section == "tools":
        return (
            "🛠️ **Tools Help**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Commands:**\n"
            "• `/stats` - Bot statistics & system info\n"
            "• `/status` - View running tasks\n"
            "• `/logs` - Download log file\n"
            "• `/cancel` - Cancel running tasks\n"
            "• `/killall` - Force stop all tasks"
        )
    # home
    return (
        "❓ **Help Menu**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a category to learn more.\n\n"
        "**Quick Start:**\n"
        "• Send a Telegram URL to download media\n"
        "• Use `/clone_range` for channel cloning\n"
        "• Send YouTube/Instagram URLs for external downloads\n\n"
        "**Tip:** Use channel IDs (`-100...`) for private channels."
    )


@bot.on_message(filters.command("start") & filters.private)
async def start(_, message: Message):
    await message.reply(
        _menu_text("main"),
        disable_web_page_preview=True,
        reply_markup=_main_menu_keyboard()
    )


@bot.on_message(filters.command("menu") & filters.private)
async def menu_cmd(_, message: Message):
    """Show main menu with inline buttons."""
    await message.reply(
        _menu_text("main"),
        disable_web_page_preview=True,
        reply_markup=_main_menu_keyboard()
    )


@bot.on_message(filters.command("help") & filters.private)
async def help_cmd(_, message: Message):
    await message.reply(
        _help_text("home"),
        disable_web_page_preview=True,
        reply_markup=_help_keyboard("home"),
    )


@bot.on_message(filters.command("ui") & filters.private)
async def ui_menu(_, message: Message):
    text = (
        "**🖥️ Channel Cloning UI**\n\n"
        "Choose an option below:\n\n"
        "1️⃣ Clone the whole channel (media + text)\n"
        "2️⃣ Clone a specific range of messages\n\n"
        "**Reply with:**\n"
        "• `1 <source> <target>` — Clone the whole channel\n"
        "• `2 <source> <target> <start_id> <end_id>` — Clone a range\n\n"
        "**Example:**\n"
        "`1 @sourcechannel @targetchannel`\n"
        "`2 @sourcechannel @targetchannel 100 200`\n\n"
        "You can also use /clone_channel and /clone_range directly."
    )
    await message.reply(text)


# FIXED: Use filters.text instead of non-existent filters.reply
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "help", "menu", "dl", "bdl", "ext", "stats", "logs", "killall", "forward", "mirror", "clone_channel", "clone_range", "ui", "status", "cancel", "cookies", "replicate"]))
async def handle_ui_reply(bot: Client, message: Message):
    # Check if this is a reply to the UI message
    if message.reply_to_message and message.reply_to_message.text and "Channel Cloning UI" in message.reply_to_message.text:
        args = message.text.split()
        if not args:
            await message.reply("Invalid input. See /ui for options.")
            return
        if args[0] == "1" and len(args) == 3:
            # Simulate /clone_channel command
            message.command = ["clone_channel"] + args[1:]
            await clone_full_channel(bot, message)
        elif args[0] == "2" and len(args) == 5:
            # Simulate /clone_range command
            message.command = ["clone_range"] + args[1:]
            await clone_range_messages(bot, message)
        else:
            await message.reply("Invalid input. See /ui for options.")
        return
    
    # Handle regular text messages (URL auto-download)
    text = message.text.strip()
    ext_url = extract_supported_url(text)
    if ext_url:
        await handle_external(bot, message, ext_url)
        return
    
    if "https://t.me/" in text:
        try:
            after_tme = text.split("https://t.me/")[-1].strip()
            if "/" not in after_tme or not after_tme.split("/")[-1].isdigit():
                await message.reply(
                    "**📎 Channel Link Detected**\n\n"
                    "This looks like a channel link. Use these commands:\n\n"
                    "• `/clone_channel <source> <target>` - Clone entire channel\n"
                    "• `/clone_range <source> <target> <start> <end>` - Clone message range\n\n"
                    f"**Examples:**\n"
                    f"• `/clone_channel {text} @yourtarget`\n"
                    f"• `/clone_range {text} @yourtarget 1 100`"
                )
                return
            await track_task(handle_download(bot, message, text))
        except Exception:
            await message.reply(
                "**❌ Invalid Link Format**\n\n"
                "Please send a valid Telegram post URL with message ID:\n"
                "• `https://t.me/channel/123`\n"
                "• `https://t.me/c/1234567890/123`"
            )
    else:
        await message.reply(
            "**📎 Send a Telegram Link**\n\n"
            "Please send a Telegram post URL or use these commands:\n\n"
            "• `/dl <post_url>` - Download specific post\n"
            "• `/clone_channel <source> <target>` - Clone entire channel\n"
            "• `/clone_range <source> <target> <start> <end>` - Clone message range"
        )


@bot.on_message(filters.command("status") & filters.private)
async def status_cmd(_, message: Message):
    running = [str(t) for t in RUNNING_TASKS if not t.done()]
    if running:
        await message.reply(f"**Running tasks:**\n{len(running)} active.")
    else:
        await message.reply("No running tasks.")


@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(_, message: Message):
    cancelled = 0
    for task in list(RUNNING_TASKS):
        if not task.done():
            task.cancel()
            cancelled += 1
    await message.reply(f"**Cancelled {cancelled} running task(s).**")


@bot.on_message(filters.command("cookies") & filters.private)
async def set_cookies(_, message: Message):
    """Save a replied cookies.txt (Netscape format) to improve YouTube / Pinterest downloads."""
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply("Reply to a cookies.txt document with /cookies")
        return
    doc = message.reply_to_message.document
    if not (doc.file_name and "cookie" in doc.file_name.lower()):
        await message.reply("File name should include 'cookie'.")
        return
    os.makedirs("cookies", exist_ok=True)
    dest = os.path.join("cookies", "cookies.txt")
    try:
        await message.reply_to_message.download(dest)
        await message.reply("✅ Cookies stored. Future external downloads will use them (until dyno restart).")
    except Exception as e:
        await message.reply(f"❌ Failed to store cookies: {e}")


async def handle_download(bot: Client, message: Message, post_url: str):
    post_url = post_url.split("?", 1)[0]

    try:
        chat_id, message_id = getChatMsgID(post_url)
        result = await user.get_messages(chat_id=chat_id, message_ids=message_id)
        
        if isinstance(result, list):
            chat_message = result[0] if result else None
        else:
            chat_message = result
        
        if not chat_message:
            await message.reply("**❌ Message not found or unable to access.**")
            return

        LOGGER(__name__).info(f"Downloading media from URL: {post_url}")

        if chat_message.document or chat_message.video or chat_message.audio:
            file_size = (
                chat_message.document.file_size if chat_message.document
                else chat_message.video.file_size if chat_message.video
                else chat_message.audio.file_size
            )
            if not await fileSizeLimit(file_size, message, "download", getattr(user.me, 'is_premium', False)):
                return

        parsed_caption = await get_parsed_msg(chat_message.caption or "", chat_message.caption_entities)
        parsed_text = await get_parsed_msg(chat_message.text or "", chat_message.entities)

        if chat_message.media_group_id:
            success = await processMediaGroup(chat_message, bot, message)
            if not success:
                await message.reply("**Could not extract any valid media from the media group.**")
            return

        if chat_message.media:
            start_time = time()
            progress_message = await message.reply("**📥 Downloading Progress...**")

            filename = get_file_name(message_id, chat_message)
            download_path = get_download_path(message.id, filename)

            media_path = await chat_message.download(
                file_name=str(download_path),
                progress=Leaves.progress_for_pyrogram,
                progress_args=progressArgs("📥 Downloading Progress", progress_message, start_time),
            )

            LOGGER(__name__).info(f"Downloaded media: {media_path}")

            media_type = (
                "photo" if chat_message.photo else
                "video" if chat_message.video else
                "audio" if chat_message.audio else
                "document"
            )

            await send_media(bot, message, media_path, media_type, parsed_caption, progress_message, start_time)

            cleanup_download(media_path)
            await progress_message.delete()

        elif chat_message.text or chat_message.caption:
            await message.reply(parsed_text or parsed_caption)
        else:
            await message.reply("**No media or text found in the post URL.**")

    except (PeerIdInvalid, BadRequest, KeyError):
        await message.reply("**Make sure the user client is part of the chat.**")
    except ValueError as e:
        if "Invalid URL format" in str(e):
            await message.reply(
                "**❌ Invalid Telegram URL**\n\n"
                "Please send a valid Telegram post URL like:\n"
                "• `https://t.me/channel/123`\n"
                "• `https://t.me/c/1234567890/123`"
            )
        else:
            await message.reply(f"**❌ Please send a valid Telegram post URL.**")
        LOGGER(__name__).error(f"URL validation error: {e}")
    except Exception as e:
        await message.reply(f"**❌ {e}**")
        LOGGER(__name__).error(e)


@bot.on_message(filters.command("dl") & filters.private)
async def download_media(bot: Client, message: Message):
    if len(message.command) < 2:
        await message.reply("**Provide a post URL after the /dl command.**")
        return
    post_url = message.command[1]
    await track_task(handle_download(bot, message, post_url))


@bot.on_message(filters.command("bdl") & filters.private)
async def download_range(bot: Client, message: Message):
    args = message.text.split()

    if len(args) != 3 or not all(arg.startswith("https://t.me/") for arg in args[1:]):
        await message.reply(
            "🚀 **Batch Download Process**\n"
            "`/bdl start_link end_link`\n\n"
            "💡 **Example:**\n"
            "`/bdl https://t.me/mychannel/100 https://t.me/mychannel/120`"
        )
        return

    try:
        start_chat, start_id = getChatMsgID(args[1])
        end_chat, end_id = getChatMsgID(args[2])
    except Exception as e:
        await message.reply(f"**❌ Error parsing links:\n{e}**")
        return

    if start_chat != end_chat:
        await message.reply("**❌ Both links must be from the same channel.**")
        return
    if start_id > end_id:
        await message.reply("**❌ Invalid range: start ID cannot exceed end ID.**")
        return

    try:
        await user.get_chat(start_chat)
    except Exception:
        pass

    prefix = args[1].rsplit("/", 1)[0]
    loading = await message.reply(f"📥 **Downloading posts {start_id}–{end_id}…**")

    downloaded = skipped = failed = 0

    for msg_id in range(start_id, end_id + 1):
        url = f"{prefix}/{msg_id}"
        try:
            result = await user.get_messages(chat_id=start_chat, message_ids=msg_id)
            
            if isinstance(result, list):
                chat_msg = result[0] if result else None
            else:
                chat_msg = result
                
            if not chat_msg:
                skipped += 1
                continue

            has_media = bool(chat_msg.media_group_id or chat_msg.media)
            has_text = bool(chat_msg.text or chat_msg.caption)
            if not (has_media or has_text):
                skipped += 1
                continue

            task = track_task(handle_download(bot, message, url))
            try:
                await task
                downloaded += 1
            except asyncio.CancelledError:
                await loading.delete()
                await message.reply(f"**❌ Batch canceled after downloading `{downloaded}` posts.**")
                return

        except Exception as e:
            failed += 1
            LOGGER(__name__).error(f"Error at {url}: {e}")

        await asyncio.sleep(3)

    await loading.delete()
    await message.reply(
        f"**✅ Batch Process Complete!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📥 **Downloaded** : `{downloaded}` post(s)\n"
        f"⏭️ **Skipped**   : `{skipped}` (no content)\n"
        f"❌ **Failed**    : `{failed}` error(s)"
    )


@bot.on_message(filters.command("stats") & filters.private)
async def stats(_, message: Message):
    uptime = get_readable_time(int(time() - PyroConf.BOT_START_TIME))
    total, used, free = shutil.disk_usage(".")
    sent = psutil.net_io_counters().bytes_sent
    recv = psutil.net_io_counters().bytes_recv
    cpu = psutil.cpu_percent(interval=0.5)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage("/").percent
    process = psutil.Process(os.getpid())

    stats_text = (
        "**≧◉◡◉≦ Bot is Up and Running successfully.**\n\n"
        f"**➜ Bot Uptime:** `{uptime}`\n"
        f"**➜ Total Disk Space:** `{get_readable_file_size(total)}`\n"
        f"**➜ Used:** `{get_readable_file_size(used)}`\n"
        f"**➜ Free:** `{get_readable_file_size(free)}`\n"
        f"**➜ Memory Usage:** `{round(process.memory_info().rss / 1024**2)} MiB`\n\n"
        f"**➜ Upload:** `{get_readable_file_size(sent)}`\n"
        f"**➜ Download:** `{get_readable_file_size(recv)}`\n\n"
        f"**➜ CPU:** `{cpu}%` | "
        f"**➜ RAM:** `{memory_percent}%` | "
        f"**➜ DISK:** `{disk_percent}%`"
    )
    await message.reply(stats_text)


@bot.on_message(filters.command("ext") & filters.private)
async def external_download_cmd(_, message: Message):
    if len(message.command) < 2:
        await message.reply("**Usage:** /ext <YouTube|Instagram|Pinterest URL>")
        return
    url = message.command[1].strip()
    await handle_external(bot, message, url)


@bot.on_message(filters.command("logs") & filters.private)
async def logs(_, message: Message):
    if os.path.exists("logs.txt"):
        await message.reply_document(document="logs.txt", caption="**Logs**")
    else:
        await message.reply("**Logs file does not exist.**")


@bot.on_message(filters.command("killall") & filters.private)
async def cancel_all_tasks(_, message: Message):
    cancelled = 0
    for task in list(RUNNING_TASKS):
        if not task.done():
            task.cancel()
            cancelled += 1
    await message.reply(f"**Cancelled {cancelled} running task(s).**")


def get_source_channel_ids():
    """Parse SOURCE_CHANNELS config to get list of channel IDs/usernames"""
    if not PyroConf.SOURCE_CHANNELS:
        return []
    return [ch.strip() for ch in PyroConf.SOURCE_CHANNELS.split(",") if ch.strip()]


async def forward_message_to_destination(message: Message):
    """Forward a message to the destination channel"""
    if not PyroConf.DESTINATION_CHANNEL:
        LOGGER(__name__).warning("No destination channel configured")
        return
        
    try:
        await user.forward_messages(
            chat_id=PyroConf.DESTINATION_CHANNEL,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
        LOGGER(__name__).info(f"Forwarded message {message.id} from {message.chat.id} to {PyroConf.DESTINATION_CHANNEL}")
    except Exception as e:
        LOGGER(__name__).error(f"Failed to forward message: {e}")


@user.on_message(filters.channel)
async def handle_channel_message(client: Client, message: Message):
    """Handle new messages from monitored channels (modular manager)."""
    if message.edit_date:
        return
    await forwarding_manager.handle_new_message(message)
    await mirror_manager.handle_new_message(message)
    # Real-time replication
    await replication_manager.handle_new_message(message)


@user.on_edited_message(filters.channel)
async def handle_channel_edited_message(client: Client, message: Message):
    await mirror_manager.handle_edited_message(message)


@bot.on_message(filters.command("forward") & filters.private)
async def manage_forwarding(_, message: Message):
    """Command to manage channel forwarding settings"""
    args = message.text.split(maxsplit=2)
    cfg = forwarding_manager.get_config()
    if len(args) == 1:
        status = "✅ Enabled" if cfg.get("forward_enabled") else "❌ Disabled"
        srcs = cfg.get("source_channels", [])
        dest = cfg.get("destination_channel") or "Not configured"
        await message.reply(
            "**📡 Forwarding Settings**\n\n"
            f"**Status:** {status}\n"
            f"**Sources:** `{', '.join(srcs) if srcs else 'None'}`\n"
            f"**Destination:** `{dest}`\n\n"
            "**Commands:**\n"
            "`/forward enable|disable`\n"
            "`/forward settarget <channel>`\n"
            "`/forward addsrc <ch1,ch2,...>`\n"
            "`/forward rmsrc <ch1,ch2,...>`\n"
            "`/forward clearsrc`\n"
        )
        return

    sub = args[1].lower()
    if sub in ("enable", "disable"):
        forwarding_manager.enable(sub == "enable")
        await message.reply(f"Forwarding {'enabled' if sub=='enable' else 'disabled'}.")
        return

    if sub == "settarget" and len(args) == 3:
        forwarding_manager.set_target(args[2])
        await message.reply("Destination set.")
        return

    if sub == "addsrc" and len(args) == 3:
        sources = [s.strip() for s in args[2].split(",") if s.strip()]
        forwarding_manager.add_sources(sources)
        await message.reply("Sources added.")
        return

    if sub == "rmsrc" and len(args) == 3:
        sources = [s.strip() for s in args[2].split(",") if s.strip()]
        forwarding_manager.remove_sources(sources)
        await message.reply("Sources removed.")
        return

    if sub == "clearsrc":
        forwarding_manager.clear_sources()
        await message.reply("All sources cleared.")
        return

    await message.reply("**Invalid command.** Use `/forward` to see options.")


@bot.on_message(filters.command("mirror") & filters.private)
async def manage_mirroring(_, message: Message):
    args = message.text.split(maxsplit=3)
    cfg = load_config()

    if len(args) == 1:
        status = "✅ Enabled" if cfg.get("mirror_enabled") else "❌ Disabled"
        rules = cfg.get("mirror_rules") if isinstance(cfg.get("mirror_rules"), dict) else {}
        if rules:
            lines = []
            for src, targets in rules.items():
                if isinstance(targets, list) and targets:
                    lines.append(f"`{src}` -> `{', '.join([str(t) for t in targets])}`")
                else:
                    lines.append(f"`{src}` -> `[]`")
            rules_text = "\n".join(lines)
        else:
            rules_text = "None"

        await message.reply(
            "**🪞 Mirroring Settings**\n\n"
            f"**Status:** {status}\n"
            f"**Rules:**\n{rules_text}\n\n"
            "**Commands:**\n"
            "`/mirror enable|disable`\n"
            "`/mirror add <source> <target1,target2,...>`\n"
            "`/mirror rm <source> [target1,target2,...]`\n"
            "`/mirror clear`\n"
        )
        return

    sub = args[1].lower()

    if sub in ("enable", "disable"):
        set_mirror_enabled(sub == "enable")
        await message.reply(f"Mirroring {'enabled' if sub=='enable' else 'disabled'}." )
        return

    if sub == "add" and len(args) >= 4:
        source = normalize_identifier(args[2])
        targets = [normalize_identifier(t) for t in args[3].split(",") if t.strip()]
        add_mirror_rule(source, targets)
        await message.reply("Mirror rule added.")
        return

    if sub == "rm" and len(args) >= 3:
        source = normalize_identifier(args[2])
        if len(args) == 3:
            remove_mirror_rule(source, None)
            await message.reply("Mirror rule removed.")
            return
        targets = [normalize_identifier(t) for t in args[3].split(",") if t.strip()]
        remove_mirror_rule(source, targets)
        await message.reply("Mirror rule updated.")
        return

    if sub == "clear":
        clear_mirror_rules()
        await message.reply("All mirror rules cleared.")
        return

    await message.reply("**Invalid command.** Use `/mirror` to see options.")


@bot.on_message(filters.command("clone_channel") & filters.private)
async def clone_full_channel(bot: Client, message: Message):
    """Clone an entire channel from source to target."""
    args = message.text.split()
    
    if len(args) != 3:
        await message.reply(
            "🔄 **Channel Cloning** (Media Only)\n\n"
            "**Usage:** `/clone_channel <source> <target>`\n\n"
            "**Examples:**\n"
            "• `/clone_channel @sourcechannel @targetchannel`\n"
            "• `/clone_channel sourcechannel targetchannel`\n"
            "• `/clone_channel https://t.me/sourcechannel https://t.me/targetchannel`\n\n"
            "**Target can be:**\n"
            "• Public channel: `@mychannel`\n"
            "• Private channel: `https://t.me/+ABC123...`\n"
            "• Public group: `@mygroup`\n"
            "• Private group: `https://t.me/+XYZ789...`\n"
            "• Chat ID: `-1001234567890`\n\n"
            "**Note:** Only forwards photos, videos, documents, and stickers.\n"
            "Text messages, captions, and audio files are skipped.\n"
            "You must have posting rights in the target."
        )
        return
    
    source_channel = args[1]
    target_channel = args[2]
    
    def normalize_channel(channel_str):
        channel = channel_str.strip()
        if channel.startswith("https://t.me/"):
            channel = channel.rstrip("/").rsplit("/", 1)[-1]
        elif channel.startswith('@'):
            channel = channel[1:]
        return channel
    
    source_channel = normalize_channel(source_channel)
    target_channel = normalize_channel(target_channel)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Cancel", callback_data="cancel"), InlineKeyboardButton("Status", callback_data="status")]
    ])
    status_msg = await message.reply("🔍 **Validating channels and permissions...**", reply_markup=keyboard)
    
    try:
        source_info = await channel_cloner.get_channel_info(source_channel)
        if not source_info:
            await status_msg.edit("❌ **Cannot access source channel. Make sure you're a member.**")
            return
        target_info = await channel_cloner.get_channel_info(target_channel)
        if not target_info:
            await status_msg.edit("❌ **Cannot access target. Make sure you have posting rights in the target channel/group.**")
            return
        await status_msg.edit(
            f"✅ **Validated Successfully!**\n\n"
            f"**Source:** {source_info['title']} ({source_info.get('type_description', 'Channel')})\n"
            f"**Target:** {target_info['title']} ({target_info.get('type_description', 'Channel')})\n\n"
            f"🚀 **Starting full channel clone...**\n"
            f"This may take a while depending on channel size.", reply_markup=keyboard
        )
        
        async def progress_callback(current_id, start_id, end_id, stats):
            if current_id % 50 == 0:
                progress_text = (
                    f"📊 **Cloning Progress**\n\n"
                    f"**Current Message:** {current_id}\n"
                    f"**Range:** {start_id} - {end_id}\n\n"
                    f"✅ **Copied:** {stats['successful']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"⏭️ **Skipped:** {stats['skipped']}\n"
                    f"📈 **Total Processed:** {stats['total']}\n"
                )
                try:
                    await status_msg.edit(progress_text, reply_markup=keyboard)
                except:
                    pass
        
        stats = await channel_cloner.clone_channel_messages(
            source_channel,
            target_channel,
            progress_callback=progress_callback,
            progress_message=status_msg,
        )
        
        final_text = (
            f"🎉 **Channel Cloning Complete!**\n\n"
            f"**Source:** {source_info['title']} ({source_info.get('type_description', 'Channel')})\n"
            f"**Target:** {target_info['title']} ({target_info.get('type_description', 'Channel')})\n\n"
            f"📊 **Final Statistics:**\n"
            f"✅ **Copied:** {stats['successful']}\n"
            f"❌ **Failed:** {stats['failed']}\n"
            f"⏭️ **Skipped:** {stats['skipped']}\n"
            f"📈 **Total Processed:** {stats['total']}\n"
        )
        await status_msg.edit(final_text)
    except Exception as e:
        await status_msg.edit(f"❌ **Error during cloning:**\n`{str(e)}`")
        LOGGER(__name__).error(f"Channel cloning error: {e}")


# FIXED: Proper indentation and logic for clone_range
@bot.on_message(filters.command("clone_range") & filters.private)
async def clone_range_messages(bot: Client, message: Message):
    """Clone a specific range of messages from one channel to another."""
    args = message.text.split()
    
    if len(args) != 5:
        await message.reply(
            "🔄 **Range Cloning** (Media Only)\n\n"
            "**Usage:** `/clone_range <source> <target> <start_id> <end_id>`\n\n"
            "**Examples:**\n"
            "• `/clone_range @source @target 100 200`\n"
            "• `/clone_range cctv5a majhewalee 8400 8500`\n"
            "• `/clone_range https://t.me/+ABC123... @mygroup 1 50`\n\n"
            "**Target can be any channel/group you have access to:**\n"
            "• Public/private channels • Public/private groups • Chat IDs\n\n"
            "**Note:** Range is inclusive. Only forwards photos, videos,\n"
            "documents, and stickers. Text and audio are skipped."
        )
        return
    
    source_channel = args[1]
    target_channel = args[2]
    
    try:
        start_id = int(args[3])
        end_id = int(args[4])
    except ValueError:
        await message.reply("❌ **Start and end IDs must be valid numbers.**")
        return
    
    if start_id > end_id:
        await message.reply("❌ **Start ID cannot be greater than end ID.**")
        return
    
    def normalize_channel(channel_str):
        channel = channel_str.strip()
        if channel.startswith("https://t.me/"):
            channel = channel.rstrip("/").rsplit("/", 1)[-1]
        elif channel.startswith('@'):
            channel = channel[1:]
        return channel
    
    source_channel = normalize_channel(source_channel)
    target_channel = normalize_channel(target_channel)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Cancel", callback_data="cancel"), InlineKeyboardButton("Status", callback_data="status")]
    ])
    status_msg = await message.reply("🔍 **Validating channels...**", reply_markup=keyboard)
    
    try:
        source_info = await channel_cloner.get_channel_info(source_channel)
        if not source_info:
            await status_msg.edit("❌ **Cannot access source channel.**")
            return
        target_info = await channel_cloner.get_channel_info(target_channel)
        if not target_info:
            await status_msg.edit("❌ **Cannot access target.**")
            return
        
        await status_msg.edit(
            f"✅ **Validated!**\n\n"
            f"**Source:** {source_info['title']}\n"
            f"**Target:** {target_info['title']}\n"
            f"**Range:** {start_id} - {end_id}\n\n"
            f"🚀 **Starting range clone...**", reply_markup=keyboard
        )
        
        async def progress_callback(current_id, start_id_p, end_id_p, stats):
            if current_id % 20 == 0:
                progress_text = (
                    f"📊 **Range Clone Progress**\n\n"
                    f"**Current:** {current_id}/{end_id_p}\n"
                    f"✅ **Copied:** {stats['successful']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"⏭️ **Skipped:** {stats['skipped']}\n"
                )
                try:
                    await status_msg.edit(progress_text, reply_markup=keyboard)
                except:
                    pass
        
        stats = await channel_cloner.clone_channel_messages(
            source_channel,
            target_channel,
            start_id=start_id,
            end_id=end_id,
            progress_callback=progress_callback,
            progress_message=status_msg,
        )
        
        final_text = (
            f"🎉 **Range Clone Complete!**\n\n"
            f"**Source:** {source_info['title']}\n"
            f"**Target:** {target_info['title']}\n"
            f"**Range:** {start_id} - {end_id}\n\n"
            f"📊 **Statistics:**\n"
            f"✅ **Copied:** {stats['successful']}\n"
            f"❌ **Failed:** {stats['failed']}\n"
            f"⏭️ **Skipped:** {stats['skipped']}\n"
            f"📈 **Total Processed:** {stats['total']}\n"
        )
        await status_msg.edit(final_text)
    except Exception as e:
        await status_msg.edit(f"❌ **Error during range cloning:**\n`{str(e)}`")
        LOGGER(__name__).error(f"Range cloning error: {e}")


# FIXED: Moved callback handler to proper scope
@bot.on_callback_query()
async def handle_inline_buttons(client, callback_query):
    data = getattr(callback_query, "data", "")
    msg = callback_query.message
    
    # Menu navigation
    if isinstance(data, str) and data.startswith("menu:"):
        section = data.split(":", 1)[1] if ":" in data else "main"
        
        if section == "close":
            try:
                if msg:
                    await msg.delete()
            except Exception:
                pass
            await callback_query.answer()
            return
        
        # Get the appropriate keyboard and text for each section
        keyboards = {
            "main": _main_menu_keyboard(),
            "downloads": _downloads_keyboard(),
            "cloning": _cloning_keyboard(),
            "forward": _forward_keyboard(),
            "mirror": _mirror_keyboard(),
            "replicate": _replication_keyboard(),
            "external": _external_keyboard(),
            "tools": _tools_keyboard(),
            "help": _help_keyboard("home"),
        }
        
        texts = {
            "main": _menu_text("main"),
            "downloads": _menu_text("downloads"),
            "cloning": _menu_text("cloning"),
            "forward": _menu_text("forward"),
            "mirror": _menu_text("mirror"),
            "replicate": _menu_text("replicate"),
            "external": _menu_text("external"),
            "tools": _menu_text("tools"),
            "help": _help_text("home"),
        }
        
        try:
            if msg:
                await msg.edit_text(
                    texts.get(section, _menu_text("main")),
                    disable_web_page_preview=True,
                    reply_markup=keyboards.get(section, _main_menu_keyboard()),
                )
            await callback_query.answer()
        except Exception:
            try:
                await callback_query.answer("Couldn't update menu.", show_alert=True)
            except Exception:
                pass
        return
    
    # Help sections
    if isinstance(data, str) and data.startswith("help:"):
        section = data.split(":", 1)[1] if ":" in data else "home"
        if section == "close":
            try:
                if msg:
                    await msg.delete()
            except Exception:
                pass
            await callback_query.answer()
            return

        try:
            if msg:
                await msg.edit_text(
                    _help_text(section),
                    disable_web_page_preview=True,
                    reply_markup=_help_keyboard(section),
                )
            await callback_query.answer()
        except Exception:
            try:
                await callback_query.answer("Couldn't update help.", show_alert=True)
            except Exception:
                pass
        return
    
    # Guide displays
    if isinstance(data, str) and data.startswith("guide:"):
        guide = data.split(":", 1)[1] if ":" in data else ""
        try:
            if msg:
                await msg.edit_text(
                    _guide_text(guide),
                    disable_web_page_preview=True,
                    reply_markup=_back_to_menu_keyboard(),
                )
            await callback_query.answer()
        except Exception:
            try:
                await callback_query.answer("Couldn't show guide.", show_alert=True)
            except Exception:
                pass
        return
    
    # Action buttons
    if isinstance(data, str) and data.startswith("action:"):
        action = data.split(":", 1)[1] if ":" in data else ""
        
        if action == "stats":
            uptime = get_readable_time(int(time() - PyroConf.BOT_START_TIME))
            total, used, free = shutil.disk_usage(".")
            sent = psutil.net_io_counters().bytes_sent
            recv = psutil.net_io_counters().bytes_recv
            cpu = psutil.cpu_percent(interval=0.5)
            memory_percent = psutil.virtual_memory().percent
            disk_percent = psutil.disk_usage("/").percent
            process = psutil.Process(os.getpid())
            
            stats_text = (
                "📊 **Bot Statistics**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"**➜ Uptime:** `{uptime}`\n"
                f"**➜ Total Disk:** `{get_readable_file_size(total)}`\n"
                f"**➜ Used:** `{get_readable_file_size(used)}`\n"
                f"**➜ Free:** `{get_readable_file_size(free)}`\n"
                f"**➜ Memory:** `{round(process.memory_info().rss / 1024**2)} MiB`\n\n"
                f"**➜ Upload:** `{get_readable_file_size(sent)}`\n"
                f"**➜ Download:** `{get_readable_file_size(recv)}`\n\n"
                f"**➜ CPU:** `{cpu}%` | **RAM:** `{memory_percent}%` | **DISK:** `{disk_percent}%`"
            )
            try:
                if msg:
                    await msg.edit_text(stats_text, reply_markup=_back_to_menu_keyboard())
                await callback_query.answer()
            except Exception:
                await callback_query.answer("Stats updated!", show_alert=True)
            return
        
        if action == "status":
            running = [t for t in RUNNING_TASKS if not t.done()]
            if running:
                await callback_query.answer(f"{len(running)} task(s) running.", show_alert=True)
            else:
                await callback_query.answer("No running tasks.", show_alert=True)
            return
        
        if action == "logs":
            await callback_query.answer("Sending logs file...")
            if os.path.exists("logs.txt"):
                await msg.reply_document(document="logs.txt", caption="**📄 Bot Logs**")
            else:
                await msg.reply("**Logs file does not exist.**")
            return
        
        if action == "killall":
            cancelled = 0
            for task in list(RUNNING_TASKS):
                if not task.done():
                    task.cancel()
                    cancelled += 1
            await callback_query.answer(f"Cancelled {cancelled} task(s).", show_alert=True)
            return
        
        if action == "fwd_status":
            cfg = forwarding_manager.get_config()
            status = "✅ Enabled" if cfg.get("forward_enabled") else "❌ Disabled"
            srcs = cfg.get("source_channels", [])
            dest = cfg.get("destination_channel") or "Not configured"
            status_text = (
                "🔄 **Forwarding Status**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"**Status:** {status}\n"
                f"**Sources:** `{', '.join(srcs) if srcs else 'None'}`\n"
                f"**Destination:** `{dest}`"
            )
            try:
                if msg:
                    await msg.edit_text(status_text, reply_markup=_forward_keyboard())
                await callback_query.answer()
            except Exception:
                await callback_query.answer("Updated!", show_alert=True)
            return
        
        if action == "mir_status":
            cfg = load_config()
            status = "✅ Enabled" if cfg.get("mirror_enabled") else "❌ Disabled"
            rules = cfg.get("mirror_rules") if isinstance(cfg.get("mirror_rules"), dict) else {}
            if rules:
                lines = []
                for src, targets in rules.items():
                    if isinstance(targets, list) and targets:
                        lines.append(f"`{src}` → `{', '.join([str(t) for t in targets])}`")
                rules_text = "\n".join(lines)
            else:
                rules_text = "None configured"
            mirror_text = (
                "🪞 **Mirror Status**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"**Status:** {status}\n\n"
                f"**Rules:**\n{rules_text}"
            )
            try:
                if msg:
                    await msg.edit_text(mirror_text, reply_markup=_mirror_keyboard())
                await callback_query.answer()
            except Exception:
                await callback_query.answer("Updated!", show_alert=True)
            return
    
    # Forward quick actions
    if isinstance(data, str) and data.startswith("fwd:"):
        action = data.split(":", 1)[1] if ":" in data else ""
        
        if action == "enable":
            forwarding_manager.enable(True)
            await callback_query.answer("✅ Forwarding enabled!", show_alert=True)
            # Refresh the menu
            cfg = forwarding_manager.get_config()
            status = "✅ Enabled" if cfg.get("forward_enabled") else "❌ Disabled"
            srcs = cfg.get("source_channels", [])
            dest = cfg.get("destination_channel") or "Not configured"
            status_text = (
                "🔄 **Auto-Forward Menu** (Many → One)\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"**Status:** {status}\n"
                f"**Sources:** `{', '.join(srcs) if srcs else 'None'}`\n"
                f"**Destination:** `{dest}`"
            )
            try:
                if msg:
                    await msg.edit_text(status_text, reply_markup=_forward_keyboard())
            except Exception:
                pass
            return
        
        if action == "disable":
            forwarding_manager.enable(False)
            await callback_query.answer("❌ Forwarding disabled.", show_alert=True)
            # Refresh the menu
            cfg = forwarding_manager.get_config()
            status = "✅ Enabled" if cfg.get("forward_enabled") else "❌ Disabled"
            srcs = cfg.get("source_channels", [])
            dest = cfg.get("destination_channel") or "Not configured"
            status_text = (
                "🔄 **Auto-Forward Menu** (Many → One)\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"**Status:** {status}\n"
                f"**Sources:** `{', '.join(srcs) if srcs else 'None'}`\n"
                f"**Destination:** `{dest}`"
            )
            try:
                if msg:
                    await msg.edit_text(status_text, reply_markup=_forward_keyboard())
            except Exception:
                pass
            return
        
        if action == "clearsrc":
            forwarding_manager.clear_sources()
            await callback_query.answer("🗑️ All sources cleared.", show_alert=True)
            return
    
    # Mirror quick actions
    if isinstance(data, str) and data.startswith("mir:"):
        action = data.split(":", 1)[1] if ":" in data else ""
        
        if action == "enable":
            set_mirror_enabled(True)
            await callback_query.answer("✅ Mirroring enabled!", show_alert=True)
            return
        
        if action == "disable":
            set_mirror_enabled(False)
            await callback_query.answer("❌ Mirroring disabled.", show_alert=True)
            return
        
        if action == "clear":
            clear_mirror_rules()
            await callback_query.answer("🗑️ All mirror rules cleared.", show_alert=True)
            return
    
    # Replication quick actions
    if isinstance(data, str) and data.startswith("rep:"):
        action = data.split(":", 1)[1] if ":" in data else ""
        
        if action == "enable":
            replication_manager.set_enabled(True)
            await callback_query.answer("✅ Replication enabled!", show_alert=True)
            # Refresh the menu
            status = replication_manager.get_status()
            enabled_str = "✅ Enabled" if status["enabled"] else "❌ Disabled"
            try:
                if msg:
                    await msg.edit_text(
                        f"📋 **Channel Replication**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"**Status:** {enabled_str}\n"
                        f"**Mappings:** {len(status['mappings'])}\n"
                        f"**Active Backfills:** {status['active_backfills']}",
                        reply_markup=_replication_keyboard()
                    )
            except Exception:
                pass
            return
        
        if action == "disable":
            replication_manager.set_enabled(False)
            await callback_query.answer("❌ Replication disabled.", show_alert=True)
            return
        
        if action == "backfill":
            await callback_query.answer("🔄 Starting backfill... Use /replicate to check status.", show_alert=True)
            mappings = replication_manager.get_mappings()
            for m in mappings:
                if m.get("enabled", True):
                    track_task(replication_manager.backfill(m["source"], m["target"]))
            return
        
        if action == "stop":
            count = replication_manager.stop_all_backfills()
            await callback_query.answer(f"🛑 Stopped {count} backfill(s).", show_alert=True)
            return
    
    # Action: rep_status
    if isinstance(data, str) and data == "action:rep_status":
        status = replication_manager.get_status()
        enabled_str = "✅ Enabled" if status["enabled"] else "❌ Disabled"
        
        mappings_text = ""
        if status["mappings"]:
            for m in status["mappings"]:
                mapping_enabled = "✅" if m.get("enabled", True) else "❌"
                mappings_text += f"\n{mapping_enabled} `{m['source']}` → `{m['target']}`\n   📊 Cloned: {m['cloned_count']}"
        else:
            mappings_text = "\nNo mappings."
        
        rep_text = (
            "📋 **Replication Status**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**Status:** {enabled_str}\n"
            f"**Active Backfills:** {status['active_backfills']}\n\n"
            f"**Mappings:**{mappings_text}"
        )
        try:
            if msg:
                await msg.edit_text(rep_text, reply_markup=_replication_keyboard())
            await callback_query.answer()
        except Exception:
            await callback_query.answer("Updated!", show_alert=True)
        return
    
    # Clone operation buttons
    if data == "cancel":
        cancelled = 0
        for task in list(RUNNING_TASKS):
            if not task.done():
                task.cancel()
                cancelled += 1
        await callback_query.answer(f"Cancelled {cancelled} running task(s).", show_alert=True)
    elif data == "status":
        running = [str(t) for t in RUNNING_TASKS if not t.done()]
        if running:
            await callback_query.answer(f"{len(running)} task(s) running.", show_alert=True)
        else:
            await callback_query.answer("No running tasks.", show_alert=True)


@bot.on_message(filters.command("replicate") & filters.private)
async def manage_replication(_, message: Message):
    """Command to manage channel replication settings."""
    args = message.text.split(maxsplit=3)
    
    if len(args) == 1:
        # Show status
        status = replication_manager.get_status()
        enabled_str = "✅ Enabled" if status["enabled"] else "❌ Disabled"
        
        mappings_text = ""
        if status["mappings"]:
            for m in status["mappings"]:
                mapping_enabled = "✅" if m.get("enabled", True) else "❌"
                mappings_text += f"\n{mapping_enabled} `{m['source']}` → `{m['target']}`\n   📊 Cloned: {m['cloned_count']} | Last ID: {m['last_synced_id']}"
        else:
            mappings_text = "\nNo mappings configured."
        
        await message.reply(
            "📡 **Channel Replication Settings**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**Status:** {enabled_str}\n"
            f"**Active Backfills:** {status['active_backfills']}\n\n"
            f"**Mappings:**{mappings_text}\n\n"
            "**Commands:**\n"
            "`/replicate enable|disable` - Toggle replication\n"
            "`/replicate add <source> <target>` - Add mapping\n"
            "`/replicate rm <source> <target>` - Remove mapping\n"
            "`/replicate backfill [source target]` - Start backfill\n"
            "`/replicate stop` - Stop all backfills\n"
            "`/replicate list` - List all mappings with stats\n"
            "`/replicate info <channel_id>` - Get channel info\n"
            "`/replicate clear` - Remove all mappings\n"
        )
        return
    
    sub = args[1].lower()
    
    if sub == "enable":
        replication_manager.set_enabled(True)
        await message.reply("✅ **Replication enabled!**\n\nReal-time monitoring is now active for all configured mappings.")
        return
    
    if sub == "disable":
        replication_manager.set_enabled(False)
        await message.reply("❌ **Replication disabled.**")
        return
    
    if sub == "add" and len(args) >= 4:
        try:
            source = int(args[2])
            target = int(args[3])
            replication_manager.add_mapping(source, target)
            await message.reply(f"✅ **Mapping added:**\n`{source}` → `{target}`")
        except ValueError:
            await message.reply("❌ **Invalid IDs.** Use numeric channel IDs (e.g., -1001234567890)")
        return
    
    if sub == "rm" and len(args) >= 4:
        try:
            source = int(args[2])
            target = int(args[3])
            if replication_manager.remove_mapping(source, target):
                await message.reply(f"✅ **Mapping removed:**\n`{source}` → `{target}`")
            else:
                await message.reply("❌ **Mapping not found.**")
        except ValueError:
            await message.reply("❌ **Invalid IDs.**")
        return
    
    if sub == "backfill":
        status_msg = await message.reply("🔄 **Starting backfill...**")
        
        if len(args) >= 4:
            # Specific source-target backfill
            try:
                source = int(args[2])
                target = int(args[3])
                
                async def progress_cb(current, total, stats):
                    if current % 20 == 0 or current == total:
                        try:
                            await status_msg.edit(
                                f"📊 **Backfill Progress**\n\n"
                                f"**Progress:** {current}/{total}\n"
                                f"✅ **Cloned:** {stats['cloned']}\n"
                                f"⏭️ **Skipped:** {stats['skipped']}\n"
                                f"❌ **Failed:** {stats['failed']}"
                            )
                        except Exception:
                            pass
                
                stats = await replication_manager.backfill(source, target, progress_callback=progress_cb)
                await status_msg.edit(
                    f"🎉 **Backfill Complete!**\n\n"
                    f"**Source:** `{source}`\n"
                    f"**Target:** `{target}`\n\n"
                    f"📊 **Statistics:**\n"
                    f"✅ **Cloned:** {stats['cloned']}\n"
                    f"⏭️ **Skipped:** {stats['skipped']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"📈 **Total:** {stats['processed']}"
                )
            except ValueError:
                await status_msg.edit("❌ **Invalid IDs.** Usage: `/replicate backfill <source_id> <target_id>`")
        else:
            # Backfill all mappings
            mappings = replication_manager.get_mappings()
            if not mappings:
                await status_msg.edit("❌ **No mappings configured.**")
                return
            
            for m in mappings:
                if m.get("enabled", True):
                    source = m["source"]
                    target = m["target"]
                    await status_msg.edit(f"🔄 **Backfilling:**\n`{source}` → `{target}`...")
                    stats = await replication_manager.backfill(source, target)
                    LOGGER(__name__).info(f"Backfill {source}->{target}: {stats}")
            
            await status_msg.edit("🎉 **All backfills complete!**\n\nRun `/replicate` to see stats.")
        return
    
    if sub == "stop":
        count = replication_manager.stop_all_backfills()
        await message.reply(f"🛑 **Stopped {count} backfill task(s).**")
        return
    
    if sub == "list":
        # List all mappings with details
        mappings = replication_manager.get_mappings()
        if not mappings:
            await message.reply("📋 **No mappings configured.**\n\nUse `/replicate add <source> <target>` to add one.")
            return
        
        text = "📋 **Replication Mappings**\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, m in enumerate(mappings, 1):
            source = m.get("source")
            target = m.get("target")
            enabled = "✅" if m.get("enabled", True) else "❌"
            stats = replication_manager.store.get_stats(source, target)
            text += f"{i}. {enabled} `{source}` → `{target}`\n"
            text += f"   📊 Cloned: {stats['cloned_count']} | Last: #{stats['last_synced_id']}\n\n"
        
        await message.reply(text)
        return
    
    if sub == "clear":
        # Clear all mappings
        replication_manager.set_mappings([])
        replication_manager.stop_all_backfills()
        await message.reply("🗑️ **All replication mappings cleared.**")
        return
    
    if sub == "info" and len(args) >= 3:
        # Get info about a channel
        try:
            chat_id = int(args[2])
            try:
                chat = await user.get_chat(chat_id)
                title = getattr(chat, "title", "Unknown")
                username = getattr(chat, "username", None)
                members = getattr(chat, "members_count", "N/A")
                chat_type = str(getattr(chat, "type", "Unknown"))
                
                await message.reply(
                    f"📡 **Channel Info**\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"**ID:** `{chat.id}`\n"
                    f"**Title:** {title}\n"
                    f"**Username:** @{username if username else 'N/A'}\n"
                    f"**Type:** {chat_type}\n"
                    f"**Members:** {members}"
                )
            except Exception as e:
                await message.reply(f"❌ **Cannot access channel:** `{e}`\n\nMake sure the user client is a member.")
        except ValueError:
            await message.reply("❌ **Invalid channel ID.** Use numeric ID (e.g., -1001234567890)")
        return
    
    await message.reply("**Invalid command.** Use `/replicate` to see options.")


async def _startup_tasks():
    """Run startup tasks after clients are connected."""
    # Initialize default replication mappings
    _init_replication_mappings()
    
    # Auto-enable replication if mappings exist
    mappings = replication_manager.get_mappings()
    if mappings and not replication_manager.is_enabled():
        replication_manager.set_enabled(True)
        LOGGER(__name__).info("Auto-enabled replication with existing mappings")
    
    LOGGER(__name__).info("Startup tasks completed. Replication is ready for real-time monitoring.")


if __name__ == "__main__":
    try:
        LOGGER(__name__).info("Bot Started!")
        with user:
            # Run startup tasks in the user client context
            user.loop.run_until_complete(_startup_tasks())
            bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as err:
        LOGGER(__name__).error(err)
    finally:
        replication_manager.stop_all_backfills()
        LOGGER(__name__).info("Bot Stopped")
