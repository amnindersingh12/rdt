# Restricted Content Downloader Telegram Bot

**Restricted Content Downloader** is an advanced Telegram bot script to download restricted content such as photos, videos, audio files, or documents from private chats or channels. It also supports copying text messages from Telegram posts.

---

## Features

- Download media: photos, videos, audio, documents.
- Supports single media posts and media groups.
- Real-time progress bar during download.
- Copies text messages or captions.
- **NEW: Channel forwarding** - Monitor specific channels and automatically forward new posts to a destination channel.

---

## Requirements

- Python 3.8+ (Python 3.11 recommended).
- Libraries: `pyrofork`, `pyleaves`, and `tgcrypto`.
- Telegram bot token.
- Telegram API ID and API Hash.
- A valid `SESSION_STRING` for the user account session.

---

## Installation

Install dependencies with:

```bash
pip install -r requirements.txt
```

---

## Configuration

1. Copy `config.env.example` to `config.env`
2. Fill in your bot credentials:
   - `BOT_TOKEN`: Get from @BotFather
   - `SESSION_STRING`: Generate using a session string generator
   - `API_ID` and `API_HASH`: Get from https://my.telegram.org

### Channel Forwarding Setup (Optional)

To enable automatic forwarding from source channels to a destination channel:

1. Set the following environment variables in `config.env`:
   ```
   SOURCE_CHANNELS=@channel1,@channel2,-1001234567890
   DESTINATION_CHANNEL=@mydestinationchannel  
   FORWARD_ENABLED=true
   ```

2. Make sure your user account (SESSION_STRING) is a member of all source and destination channels.

**Note**: 
- Use channel usernames (with @) or channel IDs (negative numbers starting with -100)
- For private channels, you must use the channel ID
- Multiple source channels can be specified, separated by commas

---

## Commands

### Media Download Commands
- `/start` - Start the bot and get welcome message
- `/dl <post_url>` - Download media from a specific Telegram post
- `/bdl <start_url> <end_url>` - Batch download from a range of posts
- Send a plain Telegram link - Auto-download without command

### Channel Forwarding Commands
- `/forward` - Show current forwarding settings
- `/forward status` - Display forwarding status and configuration
- `/forward help` - Show help for forwarding setup

### Utility Commands
- `/stats` - Show bot statistics and system info
- `/logs` - Get bot logs file
- `/killall` - Cancel all running download tasks

---
