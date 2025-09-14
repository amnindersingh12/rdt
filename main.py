import os
import shutil
import psutil
import asyncio
from time import time
import asyncio
import os
import shutil
import psutil

from pyleaves import Leaves
from pyrogram.enums import ParseMode
from pyrogram import Client, filters
from pyrogram.errors import PeerIdInvalid, BadRequest
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from helpers.utils import processMediaGroup, progressArgs, send_media
from helpers.files import get_download_path, fileSizeLimit, get_readable_file_size, get_readable_time, cleanup_download
from helpers.msg import getChatMsgID, get_file_name, get_parsed_msg
from helpers.channel import ChannelCloner
from helpers.forwarding import ForwardingManager

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

# Initialize channel cloner
channel_cloner = ChannelCloner(user, bot, delay=1.0)
forwarding_manager = ForwardingManager(user)

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
    welcome_text = (
        "👋 **Welcome!** I can download posts, clone channels (media-only), and auto-forward from many sources to one target.\n\n"
        "**Quick Commands**\n"
        "• `/dl <post_url>` — Download a single post\n"
        "• `/bdl <start_url> <end_url>` — Batch download a range\n"
        "• `/clone_channel <source> <target>` — Clone entire channel (media only)\n"
        "• `/clone_range <source> <target> <start> <end>` — Clone a message range (media only)\n"
        "• `/forward` — Configure many→one auto-forwarding\n\n"
        "**Examples**\n"
        "• `/dl https://t.me/channel/123`\n"
        "• `/bdl https://t.me/ch/100 https://t.me/ch/120`\n"
        "• `/clone_channel @source @target`\n"
        "• `/clone_range -1001234567890 @target 8400 8500`\n"
        "• `/forward settarget @mytarget` → `/forward addsrc @src1,@src2` → `/forward enable`\n\n"
        "Send a Telegram post URL any time to download it."
    )
    await message.reply(welcome_text, disable_web_page_preview=True)

async def handle_download(bot: Client, message: Message, post_url: str):
    post_url = post_url.split("?", 1)[0]  # Remove URL query parameters

    try:
        chat_id, message_id = getChatMsgID(post_url)
        result = await user.get_messages(chat_id=chat_id, message_ids=message_id)
        
        # Extract single message from result
        if isinstance(result, list):
            chat_message = result[0] if result else None
        else:
            chat_message = result
        
        if not chat_message:
            await message.reply("**❌ Message not found or unable to access.**")
            return

        LOGGER(__name__).info(f"Downloading media from URL: {post_url}")

        # Check file size limits for downloadable media types
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
                "• `https://t.me/c/1234567890/123`\n\n"
                "For channel cloning, use:\n"
                "• `/clone_channel <source> <target>`\n"
                "• `/clone_range <source> <target> <start> <end>`"
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
            result = await user.get_messages(chat_id=start_chat, message_ids=msg_id)
            
            # Extract single message from result
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

        await asyncio.sleep(3)  # Moderate request rate to avoid overload

    await loading.delete()
    await message.reply(
        f"**✅ Batch Process Complete!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📥 **Downloaded** : `{downloaded}` post(s)\n"
        f"⏭️ **Skipped**   : `{skipped}` (no content)\n"
        f"❌ **Failed**    : `{failed}` error(s)"
    )

@bot.on_message(filters.private & ~filters.command(["start", "dl", "bdl", "stats", "logs", "killall", "forward", "clone_channel", "clone_range"]))
async def handle_any_message(bot: Client, message: Message):
    # Auto-download if user sends plain text link without commands
    if message.text and not message.text.startswith("/"):
        text = message.text.strip()
        
        # Check if it's a Telegram link
        if "https://t.me/" in text:
            # Extract the part after t.me/
            try:
                after_tme = text.split("https://t.me/")[-1].strip()
                
                # Check if it's a channel-only link (no message ID)
                if "/" not in after_tme or not after_tme.split("/")[-1].isdigit():
                    await message.reply(
                        "**📎 Channel Link Detected**\n\n"
                        "This looks like a channel link. Use these commands:\n\n"
                        "• `/clone_channel <source> <target>` - Clone entire channel\n"
                        "• `/clone_range <source> <target> <start> <end>` - Clone message range\n\n"
                        "**Target can be any channel/group:**\n"
                        "• Public: `@mychannel` or `@mygroup`\n"
                        "• Private: `https://t.me/+ABC123...`\n"
                        "• Chat ID: `-1001234567890`\n\n"
                        "**Examples:**\n"
                        f"• `/clone_channel {text} @yourtarget`\n"
                        f"• `/clone_range {text} @yourtarget 1 100`"
                    )
                    return
                    
                # It has a message ID, try to download
                await track_task(handle_download(bot, message, text))
                
            except Exception:
                await message.reply(
                    "**❌ Invalid Link Format**\n\n"
                    "Please send a valid Telegram post URL with message ID:\n"
                    "• `https://t.me/channel/123`\n"
                    "• `https://t.me/c/1234567890/123`"
                )
        else:
            # Not a Telegram link
            await message.reply(
                "**📎 Send a Telegram Link**\n\n"
                "Please send a Telegram post URL or use these commands:\n\n"
                "• `/dl <post_url>` - Download specific post\n"
                "• `/clone_channel <source> <target>` - Clone entire channel\n"
                "• `/clone_range <source> <target> <start> <end>` - Clone message range"
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
    """Handle new messages from monitored channels (modular manager)."""
    if message.edit_date:
        return
    await forwarding_manager.handle_new_message(message)

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
    
    # Normalize channel identifiers - handle t.me links, @ prefixes, or plain usernames
    def normalize_channel(channel_str):
        channel = channel_str.strip()
        if channel.startswith("https://t.me/"):
            channel = channel.rstrip("/").rsplit("/", 1)[-1]
        elif channel.startswith('@'):
            channel = channel[1:]
        return channel
    
    source_channel = normalize_channel(source_channel)
    target_channel = normalize_channel(target_channel)
    
    status_msg = await message.reply("🔍 **Validating channels and permissions...**")
    
    try:
        # Validate channels
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
            f"This may take a while depending on channel size."
        )
        
        # Progress callback function
        async def progress_callback(current_id, start_id, end_id, stats):
            if current_id % 50 == 0:  # Update every 50 messages
                progress_text = (
                    f"📊 **Cloning Progress** (Media Only)\n\n"
                    f"**Current Message:** {current_id}\n"
                    f"**Range:** {start_id} - {end_id}\n\n"
                    f"✅ **Media Forwarded:** {stats['successful']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"⏭️ **Skipped (Text/Audio):** {stats['skipped']}\n"
                    f"📈 **Total Processed:** {stats['total']}\n\n"
                    f"*Note: Text, captions, and audio are filtered out*"
                )
                try:
                    await status_msg.edit(progress_text)
                except:
                    pass  # Ignore edit errors (rate limits, etc.)
        
        # Start cloning
        stats = await channel_cloner.clone_channel_messages(
            source_channel, 
            target_channel,
            progress_callback=progress_callback
        )
        
        # Final report
        final_text = (
            f"🎉 **Channel Cloning Complete!** (Media Only)\n\n"
            f"**Source:** {source_info['title']} ({source_info.get('type_description', 'Channel')})\n"
            f"**Target:** {target_info['title']} ({target_info.get('type_description', 'Channel')})\n\n"
            f"📊 **Final Statistics:**\n"
            f"✅ **Media Forwarded:** {stats['successful']}\n"
            f"❌ **Failed:** {stats['failed']}\n"
            f"⏭️ **Skipped (Text/Audio):** {stats['skipped']}\n"
            f"📈 **Total Processed:** {stats['total']}\n\n"
            f"*Note: Text, captions, and audio files were filtered out*"
        )
        await status_msg.edit(final_text)
        
    except Exception as e:
        await status_msg.edit(f"❌ **Error during cloning:**\n`{str(e)}`")
        LOGGER(__name__).error(f"Channel cloning error: {e}")

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
    
    # Normalize channel identifiers
    def normalize_channel(channel_str):
        channel = channel_str.strip()
        if channel.startswith("https://t.me/"):
            channel = channel.rstrip("/").rsplit("/", 1)[-1]
        elif channel.startswith('@'):
            channel = channel[1:]
        return channel
    
    source_channel = normalize_channel(source_channel)
    target_channel = normalize_channel(target_channel)
    
    status_msg = await message.reply(
        f"🔍 **Validating channels for range clone...**\n"
        f"**Range:** {start_id} - {end_id} ({end_id - start_id + 1} messages)"
    )
    
    try:
        # Validate channels
        source_info = await channel_cloner.get_channel_info(source_channel)
        if not source_info:
            await status_msg.edit("❌ **Cannot access source channel. Make sure you're a member.**")
            return
        
        target_info = await channel_cloner.get_channel_info(target_channel)
        if not target_info:
            await status_msg.edit("❌ **Cannot access target. Make sure you have posting rights in the target channel/group.**")
            return
        
        await status_msg.edit(
            f"✅ **Starting range clone...**\n\n"
            f"**Source:** {source_info['title']} ({source_info.get('type_description', 'Channel')})\n"
            f"**Target:** {target_info['title']} ({target_info.get('type_description', 'Channel')})\n"
            f"**Range:** {start_id} - {end_id}\n"
            f"**Total Messages:** {end_id - start_id + 1}"
        )
        
        # Progress callback function
        async def progress_callback(current_id, start_id, end_id, stats):
            if current_id % 25 == 0:  # Update every 25 messages for range clone
                progress = ((current_id - start_id + 1) / (end_id - start_id + 1)) * 100
                progress_text = (
                    f"📊 **Range Clone Progress** (Media Only)\n\n"
                    f"**Progress:** {progress:.1f}%\n"
                    f"**Current:** {current_id}/{end_id}\n\n"
                    f"✅ **Media Forwarded:** {stats['successful']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"⏭️ **Skipped (Text/Audio):** {stats['skipped']}\n\n"
                    f"*Filtering out text, captions, and audio*"
                )
                try:
                    await status_msg.edit(progress_text)
                except:
                    pass
        
        # Start range cloning
        stats = await channel_cloner.clone_channel_messages(
            source_channel, 
            target_channel,
            start_id=start_id,
            end_id=end_id,
            progress_callback=progress_callback
        )
        
        # Final report
        final_text = (
            f"🎉 **Range Clone Complete!** (Media Only)\n\n"
            f"**Source:** {source_info['title']} ({source_info.get('type_description', 'Channel')})\n"
            f"**Target:** {target_info['title']} ({target_info.get('type_description', 'Channel')})\n"
            f"**Range:** {start_id} - {end_id}\n\n"
            f"📊 **Statistics:**\n"
            f"✅ **Media Forwarded:** {stats['successful']}\n"
            f"❌ **Failed:** {stats['failed']}\n"
            f"⏭️ **Skipped (Text/Audio):** {stats['skipped']}\n"
            f"📈 **Total Processed:** {stats['total']}\n\n"
            f"*Note: Text, captions, and audio files were filtered out*"
        )
        await status_msg.edit(final_text)
        
    except Exception as e:
        await status_msg.edit(f"❌ **Error during range cloning:**\n`{str(e)}`")
        LOGGER(__name__).error(f"Range cloning error: {e}")

if __name__ == "__main__":
    try:
        LOGGER(__name__).info("Bot Started!")
        with user:
            bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as err:
        LOGGER(__name__).error(err)
    finally:
        LOGGER(__name__).info("Bot Stopped")
