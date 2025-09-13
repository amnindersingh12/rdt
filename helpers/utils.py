import os
from time import time
from PIL import Image
from logger import LOGGER
from typing import Optional
from asyncio.subprocess import PIPE
from asyncio import create_subprocess_exec, create_subprocess_shell, wait_for

from pyleaves import Leaves
from pyrogram.parser import Parser
from pyrogram.utils import get_channel_id
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    Voice,
)

from helpers.files import (
    fileSizeLimit,
    cleanup_download,
    get_download_path
)

from helpers.msg import (
    get_parsed_msg,
    get_file_name
)

# Template for progress bar display during upload/download operations
PROGRESS_BAR = """
Percentage: {percentage:.2f}% | {current}/{total}
Speed: {speed}/s
Estimated Time Left: {est_time} seconds
"""


async def cmd_exec(cmd, shell=False):
    """
    Executes a shell command asynchronously and captures output.

    Args:
        cmd (list or str): Command to execute. If shell=True, it should be a string.
        shell (bool): If True, executes the command within the shell.

    Returns:
        tuple: (stdout (str), stderr (str), return_code (int))
    """
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)

    stdout, stderr = await proc.communicate()

    try:
        stdout = stdout.decode().strip()
    except Exception:
        stdout = "Unable to decode the response!"

    try:
        stderr = stderr.decode().strip()
    except Exception:
        stderr = "Unable to decode the error!"

    return stdout, stderr, proc.returncode


async def get_media_info(path):
    """
    Retrieves media metadata such as duration, artist, and title using ffprobe.

    Args:
        path (str): Path to the media file.

    Returns:
        tuple: (duration (int seconds), artist (str or None), title (str or None))
               Returns (0, None, None) on failure or missing data.
    """
    try:
        result = await cmd_exec([
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_format", path,
        ])
    except Exception as e:
        print(f"Get Media Info: {e}. Mostly File not found! - File: {path}")
        return 0, None, None

    stdout, _, returncode = result
    if stdout and returncode == 0:
        fields = eval(stdout).get("format")  # parsing JSON string returned by ffprobe
        if not fields:
            return 0, None, None

        duration = round(float(fields.get("duration", 0)))
        tags = fields.get("tags", {})
        # Case-insensitive keys for artist and title
        artist = tags.get("artist") or tags.get("ARTIST") or tags.get("Artist")
        title = tags.get("title") or tags.get("TITLE") or tags.get("Title")

        return duration, artist, title

    return 0, None, None


async def get_video_thumbnail(video_file, duration):
    """
    Generates a thumbnail image from the midpoint of the video.

    Args:
        video_file (str): Path to the video file.
        duration (int or None): Duration of the video in seconds.

    Returns:
        str or None: Path to the generated thumbnail image or None on failure.
    """
    output = os.path.join("Assets", "video_thumb.jpg")

    # If duration not supplied, try to fetch it
    if duration is None:
        duration = (await get_media_info(video_file))[0]

    if not duration:
        # Default to 3 seconds if duration cannot be determined
        duration = 3

    # Grab a thumbnail at midpoint
    thumbnail_time = duration // 2

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(thumbnail_time), "-i", video_file,
        "-vf", "thumbnail", "-q:v", "1", "-frames:v", "1",
        "-threads", str(os.cpu_count() // 2), output,
    ]

    try:
        _, err, code = await wait_for(cmd_exec(cmd), timeout=60)
        # Validate the thumbnail was created
        if code != 0 or not os.path.exists(output):
            return None
    except Exception:
        return None

    return output


def progressArgs(action: str, progress_message, start_time):
    """
    Constructs standardized arguments for progress callback used by Leaves.

    Args:
        action (str): Action description for progress (e.g., "Uploading Progress").
        progress_message: Message object to update progress on.
        start_time (float): Timestamp marking start of operation.

    Returns:
        tuple: Arguments to pass to progress callback.
    """
    return (action, progress_message, start_time, PROGRESS_BAR, "▓", "░")


async def send_media(
    bot, message, media_path, media_type, caption, progress_message, start_time
):
    """
    Sends media (photo, video, audio, or document) to a chat with progress reporting.

    Args:
        bot: Bot instance used to send messages.
        message: Message object used to reply in chat.
        media_path (str): Path to the media file.
        media_type (str): Type of media ("photo", "video", "audio", "document").
        caption (str): Caption to accompany the media.
        progress_message: Message object for progress updates.
        start_time (float): Start time to calculate progress speed.
    """
    file_size = os.path.getsize(media_path)

    # Check if the file size respects limits, else abort sending
    if not await fileSizeLimit(file_size, message, "upload"):
        return

    # Create arguments for progress callback
    progress_args = progressArgs("📥 Uploading Progress", progress_message, start_time)

    LOGGER(__name__).info(f"Uploading media: {media_path} ({media_type})")

    if media_type == "photo":
        await message.reply_photo(
            media_path,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )
    elif media_type == "video":
        # Remove old thumbnail if exists to avoid confusion
        thumbnail_path = "Assets/video_thumb.jpg"
        if os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)

        duration = (await get_media_info(media_path))[0]
        thumb = await get_video_thumbnail(media_path, duration)

        if thumb is not None and thumb != "none":
            with Image.open(thumb) as img:
                width, height = img.size
        else:
            # Provide a default resolution if thumbnail not available
            width, height = 480, 320

        if thumb == "none":
            thumb = None

        await message.reply_video(
            media_path,
            duration=duration,
            width=width,
            height=height,
            thumb=thumb,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )
    elif media_type == "audio":
        duration, artist, title = await get_media_info(media_path)

        await message.reply_audio(
            media_path,
            duration=duration,
            performer=artist,
            title=title,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )
    elif media_type == "document":
        await message.reply_document(
            media_path,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )


async def processMediaGroup(chat_message, bot, message):
    """
    Downloads and sends a group of media messages (media group) handling errors and progress.

    Args:
        chat_message: The original message with media group attached.
        bot: Bot instance to send messages.
        message: Message object to reply and track progress.

    Returns:
        bool: True if media group was sent successfully, False otherwise.
    """
    # Fetch all messages in the media group
    media_group_messages = await chat_message.get_media_group()

    valid_media = []    # List to hold media ready for sending
    temp_paths = []     # Track temporary downloaded file paths for cleanup
    invalid_paths = []  # Track invalid or failed download paths for cleanup

    start_time = time()
    progress_message = await message.reply("📥 Downloading media group...")
    LOGGER(__name__).info(f"Downloading media group with {len(media_group_messages)} items...")

    for msg in media_group_messages:
        # Acceptable media types to process
        if msg.photo or msg.video or msg.document or msg.audio:
            try:
                # Generate a proper filename and download path for each media item
                filename = get_file_name(msg.id, msg)
                download_path = get_download_path(message.id, filename)
                
                # Download media with progress callback using proper file path
                media_path = await msg.download(
                    file_name=download_path,
                    progress=Leaves.progress_for_pyrogram,
                    progress_args=progressArgs(
                        "📥 Downloading Progress", progress_message, start_time
                    ),
                )
                temp_paths.append(media_path)

                # Convert captions with entities to plain text
                parsed_caption = await get_parsed_msg(
                    msg.caption or "", msg.caption_entities
                )

                # Create appropriate InputMedia objects based on media type
                if msg.photo:
                    valid_media.append(InputMediaPhoto(media=media_path, caption=parsed_caption))
                elif msg.video:
                    valid_media.append(InputMediaVideo(media=media_path, caption=parsed_caption))
                elif msg.document:
                    valid_media.append(InputMediaDocument(media=media_path, caption=parsed_caption))
                elif msg.audio:
                    valid_media.append(InputMediaAudio(media=media_path, caption=parsed_caption))

            except Exception as e:
                LOGGER(__name__).info(f"Error downloading media: {e}")
                
                # If we have a media path and the file exists, mark it as invalid for cleanup
                if 'media_path' in locals() and media_path and os.path.exists(media_path):
                    invalid_paths.append(media_path)
                continue

    LOGGER(__name__).info(f"Valid media count: {len(valid_media)}")

    if valid_media:
        try:
            # Try sending grouped media as an album
            await bot.send_media_group(chat_id=message.chat.id, media=valid_media)
            await progress_message.delete()

        except Exception:
            # On failure, fallback to sending media individually with error reporting
            await message.reply("**❌ Failed to send media group, trying individual uploads**")
            for media in valid_media:
                try:
                    if isinstance(media, InputMediaPhoto):
                        await bot.send_photo(chat_id=message.chat.id, photo=media.media, caption=media.caption)
                    elif isinstance(media, InputMediaVideo):
                        await bot.send_video(chat_id=message.chat.id, video=media.media, caption=media.caption)
                    elif isinstance(media, InputMediaDocument):
                        await bot.send_document(chat_id=message.chat.id, document=media.media, caption=media.caption)
                    elif isinstance(media, InputMediaAudio):
                        await bot.send_audio(chat_id=message.chat.id, audio=media.media, caption=media.caption)
                    elif isinstance(media, Voice):
                        await bot.send_voice(chat_id=message.chat.id, voice=media.media, caption=media.caption)
                except Exception as individual_e:
                    await message.reply(f"Failed to upload individual media: {individual_e}")

            await progress_message.delete()

        # Cleanup all temporary and invalid downloaded files
        for path in temp_paths + invalid_paths:
            cleanup_download(path)

        return True

    # No valid media found, inform user and cleanup invalid paths
    await progress_message.delete()
    await message.reply("❌ No valid media found in the media group.")
    for path in invalid_paths:
        cleanup_download(path)
    return False
