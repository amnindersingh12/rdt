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

# Initialize channel cloner
channel_cloner = ChannelCloner(user, bot)

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
        "👋 **Welcome!**\n\n"
        "🚀 **Available Commands:**\n"
        "• Send a Telegram post link for single download\n"
        "• `/dl <post_url>` - Download media from post\n"
        "• `/bdl <start_url> <end_url>` - Batch download posts\n"
        "• `/clone_channel <source> <target>` - Clone entire channel\n"
        "• `/clone_range <source> <target> <start_id> <end_id>` - Clone message range\n"
        "• `/stats` - Bot statistics\n"
        "• `/logs` - View logs\n"
        "• `/killall` - Cancel all running tasks\n\n"
        "**Ready?** Send me a command or Telegram post link!"
    )
    await message.reply(welcome_text, disable_web_page_preview=True)

@bot.on_message(filters.command("clone_channel") & filters.private)
async def clone_full_channel(bot: Client, message: Message):
    """Clone an entire channel from source to target."""
    args = message.text.split()
    
    if len(args) != 3:
        await message.reply(
            "🔄 **Channel Cloning**\n\n"
            "**Usage:** `/clone_channel <source_channel> <target_channel>`\n\n"
            "**Examples:**\n"
            "• `/clone_channel @sourcechannel @targetchannel`\n"
            "• `/clone_channel sourcechannel targetchannel`\n\n"
            "**Note:** You must be admin in target channel with post message rights."
        )
        return
    
    source_channel = args[1]
    target_channel = args[2]
    
    # Remove @ if present
    if source_channel.startswith('@'):
        source_channel = source_channel[1:]
    if target_channel.startswith('@'):
        target_channel = target_channel[1:]
    
    status_msg = await message.reply("🔍 **Validating channels and permissions...**")
    
    try:
        # Validate channels
        source_info = await channel_cloner.get_channel_info(source_channel)
        if not source_info:
            await status_msg.edit("❌ **Cannot access source channel. Make sure you're a member.**")
            return
        
        target_info = await channel_cloner.get_channel_info(target_channel)
        if not target_info:
            await status_msg.edit("❌ **Cannot access target channel. Make sure you're a member with admin rights.**")
            return
        
        await status_msg.edit(
            f"✅ **Channels validated!**\n\n"
            f"**Source:** {source_info['title']}\n"
            f"**Target:** {target_info['title']}\n\n"
            f"🚀 **Starting full channel clone...**\n"
            f"This may take a while depending on channel size."
        )
        
        # Progress callback function
        async def progress_callback(current_id, start_id, end_id, stats):
            if current_id % 50 == 0:  # Update every 50 messages
                progress_text = (
                    f"📊 **Cloning Progress**\n\n"
                    f"**Current Message:** {current_id}\n"
                    f"**Range:** {start_id} - {end_id}\n\n"
                    f"✅ **Successful:** {stats['successful']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"⏭️ **Skipped:** {stats['skipped']}\n"
                    f"📈 **Total Processed:** {stats['total']}"
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
            f"🎉 **Channel Cloning Complete!**\n\n"
            f"**Source:** {source_info['title']}\n"
            f"**Target:** {target_info['title']}\n\n"
            f"📊 **Final Statistics:**\n"
            f"✅ **Successful:** {stats['successful']}\n"
            f"❌ **Failed:** {stats['failed']}\n"
            f"⏭️ **Skipped:** {stats['skipped']}\n"
            f"📈 **Total Processed:** {stats['total']}"
        )
        await status_msg.edit(final_text)
        
    except Exception as e:
        await status_msg.edit(f"❌ **Error during cloning:**\n`{str(e)}`")
        LOGGER(__name__).error(f"Channel cloning error: {e}")

@bot.on_message(filters.command("clone_range") & filters.private)
async def clone_channel_range(bot: Client, message: Message):
    """Clone a specific range of messages from source to target channel."""
    args = message.text.split()
    
    if len(args) != 5:
        await message.reply(
            "🔄 **Range Cloning**\n\n"
            "**Usage:** `/clone_range <source_channel> <target_channel> <start_id> <end_id>`\n\n"
            "**Examples:**\n"
            "• `/clone_range @sourcechannel @targetchannel 100 200`\n"
            "• `/clone_range sourcechannel targetchannel 1 50`\n\n"
            "**Note:** You must be admin in target channel with post message rights."
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
    
    # Remove @ if present
    if source_channel.startswith('@'):
        source_channel = source_channel[1:]
    if target_channel.startswith('@'):
        target_channel = target_channel[1:]
    
    status_msg = await message.reply("🔍 **Validating channels and permissions...**")
    
    try:
        # Validate channels
        source_info = await channel_cloner.get_channel_info(source_channel)
        if not source_info:
            await status_msg.edit("❌ **Cannot access source channel. Make sure you're a member.**")
            return
        
        target_info = await channel_cloner.get_channel_info(target_channel)
        if not target_info:
            await status_msg.edit("❌ **Cannot access target channel. Make sure you're a member with admin rights.**")
            return
        
        await status_msg.edit(
            f"✅ **Channels validated!**\n\n"
            f"**Source:** {source_info['title']}\n"
            f"**Target:** {target_info['title']}\n"
            f"**Range:** {start_id} - {end_id}\n\n"
            f"🚀 **Starting range clone...**"
        )
        
        # Progress callback function
        async def progress_callback(current_id, start_id, end_id, stats):
            if current_id % 10 == 0:  # Update every 10 messages for smaller ranges
                progress_text = (
                    f"📊 **Cloning Progress**\n\n"
                    f"**Current Message:** {current_id}\n"
                    f"**Range:** {start_id} - {end_id}\n\n"
                    f"✅ **Successful:** {stats['successful']}\n"
                    f"❌ **Failed:** {stats['failed']}\n"
                    f"⏭️ **Skipped:** {stats['skipped']}\n"
                    f"📈 **Total Processed:** {stats['total']}"
                )
                try:
                    await status_msg.edit(progress_text)
                except:
                    pass  # Ignore edit errors
        
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
            f"🎉 **Range Cloning Complete!**\n\n"
            f"**Source:** {source_info['title']}\n"
            f"**Target:** {target_info['title']}\n"
            f"**Range:** {start_id} - {end_id}\n\n"
            f"📊 **Final Statistics:**\n"
            f"✅ **Successful:** {stats['successful']}\n"
            f"❌ **Failed:** {stats['failed']}\n"
            f"⏭️ **Skipped:** {stats['skipped']}\n"
            f"📈 **Total Processed:** {stats['total']}"
        )
        await status_msg.edit(final_text)
        
    except Exception as e:
        await status_msg.edit(f"❌ **Error during range cloning:**\n`{str(e)}`")
        LOGGER(__name__).error(f"Range cloning error: {e}")

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

@bot.on_message(filters.private & ~filters.command(["start", "dl", "bdl", "stats", "logs", "killall", "clone_channel", "clone_range"]))
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
