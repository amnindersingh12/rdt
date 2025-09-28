import asyncio
import os
import re
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Callable, Awaitable, Any, Union, Coroutine
import time
import threading
import json
import base64

from logger import LOGGER

YTDLP_IMPORT_ERROR = None
try:
    import yt_dlp as ytdlp  # type: ignore
except Exception as e:  # pragma: no cover
    YTDLP_IMPORT_ERROR = e

SUPPORTED_PATTERNS = [
    r"https?://(www\.)?youtube\.com/\S+",        # YouTube full
    r"https?://youtu\.be/\S+",                      # YouTube short
    r"https?://(www\.)?instagram\.com/\S+",        # Instagram posts/reels
    r"https?://(www\.)?pin(?:terest)?\.\S+",      # Pinterest
    r"https?://(www\.)?pin\.it/\S+",              # Pinterest short links
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SUPPORTED_PATTERNS]


def is_supported_url(url: str) -> bool:
    return any(p.search(url) for p in COMPILED_PATTERNS)


def extract_supported_url(text: str) -> Optional[str]:
    """Extract first supported external URL from arbitrary text.

    Returns the matched URL string or None.
    Strips trailing punctuation that may be adjacent in chat messages.
    """
    if not text:
        return None
    for p in COMPILED_PATTERNS:
        m = p.search(text)
        if m:
            url = m.group(0)
            # Trim common trailing punctuation
            url = url.rstrip(').,;\n\r')
            return url
    return None


async def _run_in_thread(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


ProgressCallable = Callable[[Dict[str, Any]], Union[Awaitable[None], None]]


async def download_external_media(url: str, progress_cb: Optional[ProgressCallable] = None) -> Optional[Dict]:
    """Download a single video (or first format) from supported external platforms.

    Returns dict with keys: path, title, ext, filesize (int or None)
    """
    if YTDLP_IMPORT_ERROR:
        LOGGER(__name__).error(f"yt-dlp import failed: {YTDLP_IMPORT_ERROR}")
        return {"error": f"import_failed: {YTDLP_IMPORT_ERROR}"}

    tmp_dir = Path(tempfile.mkdtemp(prefix="extdl_"))
    out_tpl = str(tmp_dir / "%(title).200s.%(ext)s")

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        LOGGER(__name__).warning("ffmpeg not found in PATH: external downloads may lose audio or stay in original container")

    # Cookies support (optional). Looks for env var or local cookies/cookies.txt
    cookiefile: Optional[str] = None
    env_cookie = os.environ.get("YTDLP_COOKIES_FILE")
    if env_cookie and os.path.isfile(env_cookie):
        cookiefile = env_cookie
    else:
        default_cookie = Path("cookies") / "cookies.txt"
        if default_cookie.exists():
            cookiefile = str(default_cookie)
    # Base64 inline cookies support (YTDLP_COOKIES_B64) to persist across Heroku restarts
    if not cookiefile:
        b64 = os.environ.get("YTDLP_COOKIES_B64")
        if b64:
            try:
                decoded = base64.b64decode(b64.encode()).decode(errors="ignore")
                os.makedirs("cookies", exist_ok=True)
                target = Path("cookies") / "cookies.txt"
                target.write_text(decoded, encoding="utf-8", errors="ignore")
                cookiefile = str(target)
                LOGGER(__name__).info("[ext] Decoded base64 cookies into cookies/cookies.txt")
            except Exception as e:
                LOGGER(__name__).warning(f"[ext] Failed to decode YTDLP_COOKIES_B64: {e}")

    ydl_opts = {
        "outtmpl": out_tpl,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "ignoreerrors": True,
        "skip_download": False,
        "nocheckcertificate": True,
        # Prefer best MP4 video + best audio first; allow larger size threshold for quality.
        "format": "(bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo*+bestaudio/bestvideo+bestaudio/best)[filesize<4G]/best",
        "merge_output_format": "mp4",
        # Postprocessors ensure audio is merged/remuxed; if already single file it passes quickly.
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
        ],
        "noplaylist": True,
        # Try multiple player clients for YouTube to dodge some gating
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
        # Add a realistic user-agent to improve compatibility (esp. Pinterest)
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    }

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
        LOGGER(__name__).info(f"[ext] Using cookies file: {cookiefile}")

    loop = asyncio.get_running_loop()

    if progress_cb:
        last_update = {"t": 0.0}
        lock = threading.Lock()

        def hook(d):  # Runs inside downloader thread
            status = d.get("status")
            now = time.time()
            if status == "downloading":
                with lock:
                    if now - last_update["t"] < 0.8:  # throttle updates
                        return
                    last_update["t"] = now
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                percent = (downloaded / total * 100) if total else None
                speed = d.get("speed")  # bytes/sec
                eta = d.get("eta")  # seconds
                data = {
                    "status": "downloading",
                    "downloaded": downloaded,
                    "total": total,
                    "percent": percent,
                    "speed": speed,
                    "eta": eta,
                }
                if percent is not None:
                    try:
                        def _dispatch():
                            try:
                                res = progress_cb(data)
                                if asyncio.iscoroutine(res):
                                    asyncio.create_task(res)
                            except Exception:
                                pass
                        loop.call_soon_threadsafe(_dispatch)
                    except Exception:
                        pass
            elif status == "finished":
                data = {"status": "finished"}
                try:
                    def _dispatch_finish():
                        try:
                            res = progress_cb(data)
                            if asyncio.iscoroutine(res):
                                asyncio.create_task(res)
                        except Exception:
                            pass
                    loop.call_soon_threadsafe(_dispatch_finish)
                except Exception:
                    pass

        ydl_opts["progress_hooks"] = [hook]

    def _download(local_opts):  # runs in thread
        if 'ytdlp' not in globals():
            return None
        with ytdlp.YoutubeDL(local_opts) as ydl:  # type: ignore[name-defined]
            info = ydl.extract_info(url, download=True)
            return info

    # Multi-tier fallback strategy. We attempt progressively simpler format strings.
    # This helps with Pinterest where certain combined format expressions fail.
    base_format = ydl_opts["format"]
    fallback_formats = [
        base_format,
        # Alternate ordering with explicit audio before generic best
        "bestvideo*+bestaudio/bestvideo+bestaudio",
        "bestaudio+bestvideo/best",
        "best[ext=mp4]/best",
        "best",
        "b",
    ]

    info = None
    errors: list[str] = []
    attempt = 0
    for fmt in fallback_formats:
        attempt += 1
        local_opts = dict(ydl_opts)
        local_opts["format"] = fmt
        # Keep postprocessors while ffmpeg present for first two attempts, then drop to reduce failures
        if attempt > 2 or not ffmpeg_path:
            local_opts.pop("postprocessors", None)
        try:
            LOGGER(__name__).info(f"[ext] Attempt {attempt} format='{fmt}' for url={url}")
            info = await _run_in_thread(_download, local_opts)
            if info:
                if attempt > 1:
                    LOGGER(__name__).info(f"[ext] Fallback attempt {attempt} succeeded with format '{fmt}'.")
                break
        except Exception as e:
            err_msg = str(e) or repr(e)
            errors.append(err_msg)
            # Decide whether to continue. If last attempt, return error.
            # Log condensed reason.
            key_err = err_msg.splitlines()[0][:200]
            LOGGER(__name__).warning(f"[ext] Attempt {attempt} failed: {key_err}")
            # Continue to next attempt automatically
            continue

    if not info:
        if not errors:
            errors.append("no_exception_text")
        LOGGER(__name__).error(f"All extract attempts failed. Errors: {errors[-3:]}")
        return {"error": f"extract_failed: {errors[-1]}"}

    # If it's a playlist-like structure, get the first entry
    if "entries" in info and info["entries"]:
        info = info["entries"][0]

    # Find the downloaded file (yt-dlp returns exact filename pattern)
    title = info.get("title") or "video"
    ext = info.get("ext", "mp4")
    # Attempt to construct expected filename
    expected = list(tmp_dir.glob(f"{title[:200]}*.{ext}"))
    if not expected:
        # Fallback: pick any file in temp dir
        candidates = list(tmp_dir.glob("*"))
        if not candidates:
            return {"error": "file_missing_after_download"}
        file_path = candidates[0]
    else:
        file_path = expected[0]

    size = file_path.stat().st_size if file_path.exists() else None

    # Probe audio stream presence for video containers
    audio_missing = False
    if ffmpeg_path and file_path.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"):
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "json", str(file_path)
            ]
            proc = await asyncio.create_subprocess_exec(*probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            if out:
                meta = json.loads(out.decode() or '{}')
                streams = meta.get("streams") or []
                if not streams:
                    audio_missing = True
        except Exception:
            pass

    # If audio missing attempt one recovery re-download with broad format
    if audio_missing and ffmpeg_path:
        LOGGER(__name__).warning(f"[ext] No audio stream detected; attempting recovery download for {url}")
        try:
            tmp_dir2 = Path(tempfile.mkdtemp(prefix="extdl_fix_"))
            recover_opts = dict(ydl_opts)
            recover_opts["outtmpl"] = str(tmp_dir2 / "%(title).200s.%(ext)s")
            recover_opts["format"] = "bestaudio+bestvideo/best"
            # Keep postprocessors for recovery if ffmpeg exists
            if not ffmpeg_path:
                recover_opts.pop("postprocessors", None)
            info2 = await _run_in_thread(_download, recover_opts)
            if info2:
                # Update file target
                candidates2 = list(tmp_dir2.glob("*"))
                if candidates2:
                    file_path2 = candidates2[0]
                    if file_path2.exists() and file_path2.stat().st_size > 0:
                        # Replace original
                        try:
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                        except Exception:
                            pass
                        tmp_dir = tmp_dir2
                        file_path = file_path2
                        size = file_path.stat().st_size
                        audio_missing = False  # assume recovered
                        LOGGER(__name__).info("[ext] Audio recovery succeeded.")
        except Exception as e:
            LOGGER(__name__).warning(f"[ext] Audio recovery failed: {e}")

    return {
        "path": str(file_path),
        "title": title,
        "ext": ext,
        "filesize": size,
        "tmp_dir": str(tmp_dir),
        "audio_checked": True,
        "audio_missing": audio_missing,
        "used_cookies": bool(cookiefile),
    }


async def cleanup_external(result: Dict):
    if not result:
        return
    try:
        tmp_dir = result.get("tmp_dir")
        if tmp_dir and os.path.isdir(tmp_dir):
            for root, _, files in os.walk(tmp_dir, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass
            try:
                os.rmdir(tmp_dir)
            except Exception:
                pass
    except Exception as e:
        LOGGER(__name__).warning(f"External cleanup issue: {e}")
