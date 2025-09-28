# Architecture Overview

This document explains the main components of the Telegram Media Bot & Channel Cloner codebase and how they interact.

## High-Level Goals
- Download Telegram media (single posts and media groups)
- Clone channels (media-only, removing forwarded headers)
- Auto-forward from many source channels into one destination
- Fetch external media (YouTube / Instagram / Pinterest) with format normalization
- Provide consistent output formats (MP4 for video, PNG for images)

## Core Runtime Processes
1. **Bot Client (`bot`)**: Handles user-issued commands and direct messages.
2. **User Client (`user`)**: Authorized user session for accessing/reading channels and restricted content.
3. **Task Tracking**: `RUNNING_TASKS` set keeps references to async tasks to allow cancellation (`/killall`).
4. **Environment Checks**: `_env_checks()` ensures ffmpeg availability for media merging and transcoding.

## Module Breakdown
| Module | Purpose | Key Elements |
|--------|---------|--------------|
| `main.py` | Entry point, command handlers, lifecycle, task orchestration | `/dl`, `/bdl`, `/ext`, `/clone_channel`, `/clone_range`, `/forward`, `/cookies` |
| `helpers/channel.py` | Channel cloning logic (media-only) with fallback to download+reupload | `ChannelCloner`, `_copy_single_message`, `_download_and_reupload` |
| `helpers/forwarding.py` | Many→one channel forwarding manager | Source management, enable/disable forwarding |
| `helpers/external.py` | External URL detection + robust multi-tier yt-dlp fallback pipeline | `download_external_media`, cookies decode, audio recovery |
| `helpers/external_handler.py` | User-facing wrapper for external downloads (progress + conversion + captions) | `handle_external` |
| `helpers/convert.py` | Media normalization (mp4/png) using ffmpeg + Pillow | `ensure_mp4`, `ensure_png`, `normalize_media` |
| `helpers/files.py` | File size limits, path helpers, cleanup | `fileSizeLimit`, `get_download_path` |
| `helpers/msg.py` | Parsing Telegram entities into formatted text | `get_parsed_msg` |
| `helpers/utils.py` | Progress reporting, exec helpers, album processing | `processMediaGroup`, `send_media` |
| `logger.py` | Central logger factory | `LOGGER` |
| `config.py` | Loads Configuration (`config.env`) into `PyroConf` | Environment variable parsing |

## External Download Pipeline
```
User sends URL -> external_handler.handle_external ->
  external.is_supported_url -> download_external_media()
    -> format attempt loop (multi-tier)
    -> optional audio probe & recovery
  -> conversion (ensure_mp4 / ensure_png)
  -> upload via send_video / send_photo / send_document
  -> cleanup temp directory
```

### Fallback Strategy (yt-dlp)
Attempts formats in descending preference (combined best → simpler) until success. Postprocessors (ffmpeg merge) kept for first two attempts then dropped to reduce failure if ffmpeg missing.

### Audio Recovery
If video container lacks audio stream and ffmpeg is present, a broader `bestaudio+bestvideo/best` re-download is attempted.

## Channel Cloning Workflow
1. Resolve identifiers (`_normalize_channel_identifier`).
2. Iterate message IDs or full history.
3. Skip non-media (text/audio/voice) for cleanliness.
4. Copy if allowed; otherwise download & re-upload (protected channels).
5. Normalize formats pre-upload.
6. Progress and statistics aggregated.

## Forwarding Manager
Continuously listens for new posts in source channels via the *user* client and republishes them to the configured destination using the bot (copy or re-upload semantics defined in forwarding module).

## Configuration (`config.env`)
Essential keys:
```
API_ID=...
API_HASH=...
BOT_TOKEN=...
SESSION_STRING=...
# Forwarding (optional)
SOURCE_CHANNELS=@c1,@c2,-100123...
DESTINATION_CHANNEL=@mytarget
FORWARD_ENABLED=true
```

Optional runtime environment variables:
```
YTDLP_COOKIES_FILE=/app/path/to/cookies.txt
YTDLP_COOKIES_B64=Base64EncodedCookiesText
```

## Media Normalization
- Videos transcoded (or remuxed) to MP4 when not already `.mp4`.
- Images converted to PNG via Pillow.
- If ffmpeg missing -> original video container retained.

## Error Handling Patterns
- External downloads return dict with `error` key for structured failure (`import_failed`, `extract_failed`, `file_missing_after_download`, etc.).
- Cloning skips missing or unsupported messages gracefully.
- FloodWait exceptions are logged with delay instructions.

## Logging
- Per-module logger via `LOGGER(__name__)`.
- Fallback attempts and recovery actions logged with `[ext]` prefix.
- Warnings for missing ffmpeg, audio absence, cookie decode failures.

## Extensibility Points
Add a new external site:
1. Extend `SUPPORTED_PATTERNS` in `helpers/external.py`.
2. Optionally refine format selectors for that domain.
3. Add domain-specific post-download adjustments (e.g., thumbnails).

Add new bot command:
1. Implement handler in `main.py` with `@bot.on_message(filters.command("newcmd"))`.
2. Keep logic small; delegate to a helper module if complexity grows.

## Testing Focus Areas
- URL detection: `is_supported_url`, `extract_supported_url`.
- Fallback ordering does not raise unhandled exceptions.
- Audio recovery sets `audio_missing` correctly.

## Future Improvements (Roadmap)
- /extdebug diagnostic command
- Additional platforms (TikTok, Twitter)
- Health endpoint or `/health` command
- Structured plugin system for external extractors
- Persistent cookie refresh automation

---
Feel free to contribute—see `CONTRIBUTING.md` for workflow guidelines.
