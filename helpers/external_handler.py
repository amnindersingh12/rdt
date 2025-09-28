import os
import asyncio
from typing import Optional, Dict, Any
from pyrogram import Client
from pyrogram.types import Message

from helpers.external import is_supported_url, download_external_media, cleanup_external
from helpers.convert import ensure_mp4, ensure_png
from helpers.files import fileSizeLimit
from logger import LOGGER

# Reusable progress bar formatter
def _format_progress(data: Dict[str, Any]) -> str:
    if data.get("status") == "finished":
        return "✅ Download complete. Preparing upload..."
    downloaded = data.get("downloaded", 0)
    total = data.get("total") or 0
    percent = data.get("percent")
    speed = data.get("speed") or 0
    eta = data.get("eta")
    if total and percent is not None:
        bar_len = 18
        filled = int(bar_len * percent / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        human_dl = _human_size(downloaded)
        human_total = _human_size(total) if total else "?"
        spd = f"{_human_size(speed)}/s" if speed else "?"
        eta_s = _human_time(eta) if eta is not None else "?"
        return (
            f"📥 **External Downloading**\n"
            f"`[{bar}]` {percent:.1f}%\n"
            f"**Size:** {human_dl}/{human_total} | **Speed:** {spd} | **ETA:** {eta_s}"
        )
    else:
        return "📥 Initializing external download..."

def _human_size(n: int) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TiB"

def _human_time(seconds: int) -> str:
    if seconds is None:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

async def handle_external(bot: Client, message: Message, url: str):
    if not is_supported_url(url):
        await message.reply("❌ Unsupported URL. Supported: YouTube, Instagram, Pinterest")
        return
    status = await message.reply("🔄 Fetching external media...")

    async def progress_cb(data: Dict[str, Any]):
        try:
            await status.edit(_format_progress(data))
        except Exception:
            pass

    result = await download_external_media(url, progress_cb=progress_cb)
    if not result:
        await status.edit("❌ Failed to download external media.")
        return
    if result.get("error"):
        await status.edit(f"❌ Download error: `{result['error']}`")
        await cleanup_external(result)
        return
    path = result.get("path") or ""
    size = result.get("filesize")
    title = result.get("title")
    if size and not await fileSizeLimit(size, status, "upload"):
        await cleanup_external(result)
        return
    try:
        if not path or not os.path.exists(path):
            await status.edit("❌ Downloaded file missing.")
            await cleanup_external(result)
            return
        lower = path.lower()
        video_exts = (".mp4", ".mkv", ".webm", ".mov", ".gif")
        image_exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
        send_path = path
        try:
            if lower.endswith(video_exts):
                # Force MP4
                send_path = await ensure_mp4(path)
                await bot.send_video(chat_id=message.chat.id, video=send_path, caption=f"{title}" if title else "")
            elif lower.endswith(image_exts):
                # Force PNG
                send_path = await ensure_png(path)
                await bot.send_photo(chat_id=message.chat.id, photo=send_path, caption=f"{title}" if title else "")
            else:
                await bot.send_document(chat_id=message.chat.id, document=send_path, caption=f"{title}" if title else "")
        finally:
            # If we created a converted temp file distinct from original, remove it after send
            if send_path != path:
                try:
                    os.remove(send_path)
                except Exception:
                    pass
        try:
            await status.delete()
        except Exception:
            pass
    except Exception as e:
        await status.edit(f"❌ Upload failed: {e}")
        LOGGER(__name__).error(f"External upload error: {e}")
    finally:
        await cleanup_external(result)
