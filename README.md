# Telegram Channel Cloning & Content Downloader Bot

**Telegram Channel Cloning Bot** is an advanced Telegram bot script to download restricted content and clone entire Telegram channels. It supports downloading photos, videos, audio files, documents from private chats or channels, copying text messages, and cloning complete channels with all their content.

---

## Features

### Content Downloading
- Download media: photos, videos, audio, documents.
- Supports single media posts and media groups.
- Real-time progress bar during download.
- Copies text messages or captions.

### Channel Cloning (NEW!)
- Clone entire Telegram channels to another channel.
- Clone specific message ranges from channels.
- Batch processing with progress tracking.
- Rate limiting to prevent API floods.
- Error handling and retry mechanisms.
- Support for all media types in cloning.

---

## Requirements

- Python 3.8+ (Python 3.11 recommended).
- Libraries: `pyrofork`, `pyleaves`, and `tgcrypto`.
- Telegram bot token.
- Telegram API ID and API Hash.
- A valid `SESSION_STRING` for the user account session.
- **For channel cloning:** Admin privileges in target channels with post message rights.

---

## Installation

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Configuration

1. Create a `config.env` file with the following variables:

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
SESSION_STRING=your_session_string
```

2. Generate a session string using `pyrogram` session generator.

---

## Usage

### Available Commands

- **Single Download:** Send a Telegram post link directly to the bot
- **`/dl <post_url>`** - Download media from a specific post
- **`/bdl <start_url> <end_url>`** - Batch download posts in a range
- **`/clone_channel <source> <target>`** - Clone entire channel (NEW!)
- **`/clone_range <source> <target> <start_id> <end_id>`** - Clone message range (NEW!)
- **`/stats`** - View bot statistics
- **`/logs`** - View bot logs
- **`/killall`** - Cancel all running tasks

### Channel Cloning Examples

**Clone entire channel:**
```
/clone_channel @sourcechannel @targetchannel
```

**Clone specific message range:**
```
/clone_range @sourcechannel @targetchannel 100 200
```

### Important Notes for Channel Cloning

1. **Permissions Required:**
   - You must be a member of the source channel
   - You must be an admin in the target channel with "Post Messages" permission

2. **Rate Limiting:**
   - The bot automatically handles rate limits
   - Includes delays between messages to prevent API floods

3. **Error Handling:**
   - Skips deleted or inaccessible messages
   - Continues processing even if some messages fail
   - Provides detailed statistics upon completion

---

