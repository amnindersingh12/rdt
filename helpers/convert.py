import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
import shutil

from logger import LOGGER

FFMPEG_NOT_FOUND = None

def _which(cmd: str) -> Optional[str]:
    from shutil import which
    return which(cmd)

# Detect ffmpeg availability once
if not _which("ffmpeg"):
    FFMPEG_NOT_FOUND = "ffmpeg executable not found in PATH"

async def ensure_mp4(input_path: str) -> str:
    """
    Ensure a video file is in mp4 (H.264/AAC) container. If already .mp4, return original.
    Otherwise transcode using ffmpeg (copy streams if possible, else re-encode).
    On failure, returns original path.
    """
    try:
        if not os.path.isfile(input_path):
            return input_path
        lower = input_path.lower()
        if lower.endswith('.mp4'):
            return input_path
        if FFMPEG_NOT_FOUND:
            LOGGER(__name__).warning(f"Skipping conversion (mp4) - {FFMPEG_NOT_FOUND}")
            return input_path
        out_path = str(Path(tempfile.gettempdir()) / (Path(input_path).stem + '_conv.mp4'))
        cmd = [
            'ffmpeg','-y','-i', input_path,
            '-c:v','libx264','-preset','veryfast','-crf','23',
            '-c:a','aac','-b:a','128k','-movflags','+faststart',
            out_path
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        return input_path
    except Exception as e:
        LOGGER(__name__).warning(f"ensure_mp4 failed: {e}")
        return input_path

async def ensure_png(input_path: str) -> str:
    """Ensure an image is in PNG format. If already .png, return original; else convert via Pillow."""
    try:
        if not os.path.isfile(input_path):
            return input_path
        lower = input_path.lower()
        if lower.endswith('.png'):
            return input_path
        # Basic heuristic: treat as image if Pillow can open it
        with Image.open(input_path) as im:
            out_path = str(Path(tempfile.gettempdir()) / (Path(input_path).stem + '_conv.png'))
            im.save(out_path, format='PNG')
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        return input_path
    except Exception as e:
        LOGGER(__name__).warning(f"ensure_png failed: {e}")
        return input_path

async def normalize_media(path: str, is_video: bool, is_image: bool) -> str:
    """
    Convenience wrapper to choose ensure_mp4 / ensure_png.
    is_image should be True for photos (Telegram photos) or image documents.
    is_video True for videos.
    """
    if is_video:
        return await ensure_mp4(path)
    if is_image:
        return await ensure_png(path)
    return path
