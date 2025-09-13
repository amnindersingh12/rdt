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

from config import PyroConf
from logger import LOGGER

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

RUNNING_TASKS = set()

def track_task(coro):
    """Create and track an asyncio task."""
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    def _cleanup(_):
        RUNNING_TASKS.discard(task)
    task.add_done_callback(_cleanup)
    return task

@bot.on_message(filters.command("start") & filters.private)
async def start(_, message: Message):
    welcome_text = "👋 **How You**\n\nReady? Send me a Telegram post link!"
    await message.reply(welcome_text, disable_web_page_preview=True)

async def handle_download(bot: Client, message: Message, post_url: str):
    post_url = post_url.split("?", 1)[0]  # Remove URL query parameters

    try:
        chat_id, message_id = getChatMsgID(post_url)
        chat_message = await user.get_messages(chat_id=chat_id, message_ids=message_id)

        LOGGER(__name__).info(f"Downloading media from URL: {post_url}")

        # Check file size limits for downloadable media types
        if chat_message.document or chat_message.video or chat_message.audio:
            file_size = (
                chat_message.document.file_size if chat_message.document
                else chat_message.video.file_size if chat_message.video
                else chat_message.audio.file_size
            )
            if not await fileSizeLimit(file_size, message, "download", user.me.is_premium):
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
                file_name=download_path,
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

    # Ensure user client is a member (safeguard)
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
            chat_msg = await user.get_messages(chat_id=start_chat, message_ids=msg_id)
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

        await asyncio.sleep(3)  # Moderate request rate to avoid overload

    await loading.delete()
    await message.reply(
        f"**✅ Batch Process Complete!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📥 **Downloaded** : `{downloaded}` post(s)\n"
        f"⏭️ **Skipped**   : `{skipped}` (no content)\n"
        f"❌ **Failed**    : `{failed}` error(s)"
    )

@bot.on_message(filters.private & ~filters.command(["start", "dl", "bdl", "stats", "logs", "killall", "forward"]))
async def handle_any_message(bot: Client, message: Message):
    # Auto-download if user sends plain text link without commands
    if message.text and not message.text.startswith("/"):
        await track_task(handle_download(bot, message, message.text))

@bot.on_message(filters.command("stats") & filters.private)
async def stats(_, message: Message):
    uptime = get_readable_time(time() - PyroConf.BOT_START_TIME)
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

# Channel forwarding functionality
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
        # Forward the message to the destination channel
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
    """Handle new messages from monitored channels"""
    if not PyroConf.FORWARD_ENABLED:
        return
    
    # Skip edited messages
    if message.edit_date:
        return
        
    source_channels = get_source_channel_ids()
    if not source_channels:
        return
    
    # Check if message is from one of our monitored channels
    channel_match = False
    for source_channel in source_channels:
        try:
            # Handle both username and ID formats
            if source_channel.startswith("@"):
                source_channel = source_channel[1:]  # Remove @ prefix
            
            # Check if the message is from this source channel
            if (str(message.chat.id) == str(source_channel) or 
                str(message.chat.username) == str(source_channel) or
                message.chat.username == source_channel):
                channel_match = True
                break
        except Exception as e:
            LOGGER(__name__).error(f"Error checking channel {source_channel}: {e}")
            continue
    
    if channel_match:
        await forward_message_to_destination(message)

@bot.on_message(filters.command("forward") & filters.private)
async def manage_forwarding(_, message: Message):
    """Command to manage channel forwarding settings"""
    args = message.text.split()
    
    if len(args) == 1:
        # Show current settings
        source_channels = get_source_channel_ids()
        status = "✅ Enabled" if PyroConf.FORWARD_ENABLED else "❌ Disabled"
        
        settings_text = (
            f"**📡 Channel Forwarding Settings**\n\n"
            f"**Status:** {status}\n"
            f"**Source Channels:** `{', '.join(source_channels) if source_channels else 'None configured'}`\n"
            f"**Destination Channel:** `{PyroConf.DESTINATION_CHANNEL if PyroConf.DESTINATION_CHANNEL else 'None configured'}`\n\n"
            f"**Commands:**\n"
            f"`/forward status` - Show current settings\n"
            f"`/forward help` - Show help information"
        )
        await message.reply(settings_text)
        return
    
    if args[1] == "status":
        source_channels = get_source_channel_ids()
        status = "✅ Enabled" if PyroConf.FORWARD_ENABLED else "❌ Disabled"
        
        settings_text = (
            f"**📡 Forwarding Status**\n\n"
            f"**Status:** {status}\n"
            f"**Source Channels:** `{len(source_channels)} configured`\n"
            f"**Destination:** `{PyroConf.DESTINATION_CHANNEL if PyroConf.DESTINATION_CHANNEL else 'Not configured'}`"
        )
        await message.reply(settings_text)
    
    elif args[1] == "help":
        help_text = (
            f"**📡 Channel Forwarding Help**\n\n"
            f"**Setup via Environment Variables:**\n"
            f"`SOURCE_CHANNELS` - Comma-separated list of source channel usernames or IDs\n"
            f"`DESTINATION_CHANNEL` - Target channel username or ID\n"
            f"`FORWARD_ENABLED` - Set to 'true' to enable forwarding\n\n"
            f"**Example:**\n"
            f"`SOURCE_CHANNELS=@channel1,@channel2,-1001234567890`\n"
            f"`DESTINATION_CHANNEL=@mychannel`\n"
            f"`FORWARD_ENABLED=true`\n\n"
            f"**Note:** The user session must be a member of all source and destination channels."
        )
        await message.reply(help_text)
    
    else:
        await message.reply("**Invalid command. Use `/forward help` for available options.**")

if __name__ == "__main__":
    try:
        LOGGER(__name__).info("Bot Started!")
        user.start()
        bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as err:
        LOGGER(__name__).error(err)
    finally:
        LOGGER(__name__).info("Bot Stopped")
