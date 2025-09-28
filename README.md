# Telegram Media Bot & Channel Cloner

**Advanced Telegram Bot** for downloading media content and cloning entire channels. Features include downloading restricted content, batch operations, automatic channel forwarding, and comprehensive channel cloning with media filtering.

---

## 🌟 Features

### Media Download
- Download photos, videos, audio, documents from any Telegram post
- Support for single media posts and media groups
- Real-time progress tracking during downloads
- Copy text messages and captions
- Batch download from message ranges

### Channel Operations
- **Channel Cloning**: Clone entire channels or specific message ranges
- **Media-Only Filtering**: Clone only photos, videos, documents, and stickers (no text/audio)
- **Protected Channel Support**: Bypass forwarding restrictions with download+reupload
- **Automatic Forwarding**: Monitor channels and auto-forward new posts
- **Universal Target Support**: Clone to public/private channels, groups, or chats

### Advanced Features
- Handle protected channels that restrict forwarding
- Support for private channels and groups via invite links
- Progress tracking with detailed statistics
- Robust error handling and retry mechanisms
- Rate limit protection with automatic delays

---

## 📋 Requirements

- Python 3.8+ (Python 3.11 recommended)
- Required libraries: `pyrofork`, `pyleaves`, `tgcrypto`
- Telegram bot token from @BotFather
- Telegram API ID and API Hash from https://my.telegram.org
- Valid `SESSION_STRING` for user account session

---

## 🚀 Installation

```bash
# Clone repository
git clone https://github.com/amnindersingh12/rdt.git
cd rdt

# Install dependencies
pip install -r requirements.txt

# Setup configuration
cp config.env.example config.env
# Edit config.env with your credentials
```

### Generate Session String (recommended)

Use the built-in helper to interactively log in and save `SESSION_STRING` to `config.env`:

```bash
python tools/generate_session.py
```

This will prompt for your phone/login code (and 2FA if enabled) and write `SESSION_STRING` into `config.env` for the bot to use.

---

## ⚙️ Configuration

### Basic Setup

1. **Get Bot Token**: Create a bot via @BotFather
2. **Get API Credentials**: Visit https://my.telegram.org
3. **Generate Session String**: Use a session string generator
4. **Configure**: Edit `config.env`:

```env
API_ID=your_api_id
API_HASH=your_api_hash  
BOT_TOKEN=your_bot_token
SESSION_STRING=your_session_string
```

### Channel Operations Setup

For automatic forwarding and channel cloning:

```env
# Optional: Auto-forwarding specific channels
SOURCE_CHANNELS=@channel1,@channel2,-1001234567890
DESTINATION_CHANNEL=@mydestinationchannel
FORWARD_ENABLED=true
```

**Important Notes:**
- Your user account must be a member of all source channels
- You need posting rights in destination channels/groups
- Use `@username` for public channels or `-100xxxxxxxxx` for channel IDs
- Private channels require channel IDs or invite links

---

## 🤖 Commands

### Media Download
- `/start` - Start the bot and get welcome message
- `/dl <post_url>` - Download media from specific Telegram post
- `/bdl <start_url> <end_url>` - Batch download from message range
- **Auto-download**: Send any post URL directly (no command needed)

### Channel Cloning (Media Only)
- `/clone_channel <source> <target>` - Clone entire channel (photos, videos, documents, stickers only)
- `/clone_range <source> <target> <start_id> <end_id>` - Clone specific message range

### Channel Forwarding (Many → One)
- `/forward` — Show settings and available subcommands
- `/forward enable|disable` — Toggle auto-forwarding
- `/forward settarget <channel>` — Set destination (e.g., `@mytarget` or `-100123...`)
- `/forward addsrc <ch1,ch2,...>` — Add one or more source channels
- `/forward rmsrc <ch1,ch2,...>` — Remove source channels
- `/forward clearsrc` — Clear all sources

### Utility
- `/stats` - Bot statistics and system information
- `/logs` - Download bot logs file
- `/killall` - Cancel all running tasks

---

## 📖 Usage Examples

### Channel Cloning

```bash
# Clone entire channel (media only)
/clone_channel @sourcechannel @mytarget

# Clone to private group (invite link)
/clone_channel @source https://t.me/+ABC123DEF

# Clone specific message range  
/clone_range @source @target 100 200

# Clone from protected channel (IDs also work)
/clone_range -1001234567890 @target 8400 8500
```

### Target Types Supported

- **Public Channel**: `@mychannel`
- **Private Channel**: `https://t.me/+ABC123...`
- **Public Group**: `@mygroup`  
- **Private Group**: `https://t.me/+XYZ789...`
- **Chat ID**: `-1001234567890`

### Media Download

```bash
# Download single post
/dl https://t.me/channel/123

# Batch download range
/bdl https://t.me/channel/100 https://t.me/channel/200

# Auto-download (just send the link)
https://t.me/channel/456
```

---

## 🔧 Key Features

### Protected Channel Support
- Automatically detects channels that restrict forwarding
- Falls back to download+reupload method
- Maintains all media quality and format
- Removes "Forwarded from" attribution

### Media Filtering
- **Forwards**: Photos, videos, documents, stickers, video notes
- **Skips**: Text messages, captions, audio files, voice messages
- **Result**: Clean media-only channels without text clutter

### Progress Tracking
```
📊 Cloning Progress (Media Only)
✅ Media Forwarded: 45
❌ Failed: 2  
⏭️ Skipped (Text/Audio): 123
📈 Total Processed: 170
```

### Error Resilience
- Continues operation despite individual message failures
- Automatic retry for rate limits
- Detailed error logging
- Graceful handling of missing/deleted messages

### Format Normalization
All cloned or externally downloaded media is normalized for consistency:
* Videos converted to MP4 (H.264/AAC) when not already MP4.
* Images converted to PNG when not already PNG.

If `ffmpeg` is not installed the bot silently falls back to original formats.

Benefits:
* Predictable extensions in target channels
* Broad client compatibility
* Reduced playback/preview issues from exotic containers

Install ffmpeg:
* Windows: Download static build https://www.gyan.dev/ffmpeg/ (add `bin` to PATH)
* macOS: `brew install ffmpeg`
* Debian/Ubuntu: `sudo apt-get update && sudo apt-get install -y ffmpeg`
* Alpine: `apk add --no-cache ffmpeg`

No reconfiguration needed—detection happens automatically at runtime.

---

## ☁️ Heroku Deployment

### Buildpacks Order (Critical)
Ensure the Apt buildpack precedes the Python buildpack so `ffmpeg` installs before Python dependencies build:

1. heroku-community/apt  
2. heroku/python  

Check:
```bash
heroku buildpacks -a <app>
```
Set/Reorder:
```bash
heroku buildpacks:clear -a <app>
heroku buildpacks:add --index 1 heroku-community/apt -a <app>
heroku buildpacks:add --index 2 heroku/python -a <app>
```

### Aptfile
The root `Aptfile` must contain:
```
ffmpeg
```
Trigger a new build after changes:
```bash
git add Aptfile
git commit -m "Ensure Aptfile for ffmpeg"
git push heroku main
```

### Verifying ffmpeg on Dyno
```bash
heroku run bash -a <app>
which ffmpeg
ffmpeg -version
```
If not found, re-check buildpack order and line endings of `Aptfile` (should be LF, not CRLF).

### Runtime Detection
Startup logs show either:
```
ffmpeg detected at /app/.apt/usr/bin/ffmpeg
```
or a warning if missing.

---

## 🌐 External Downloads (YouTube / Instagram / Pinterest)

The bot auto-detects supported URLs even without `/ext`. For best results:

### 1. Provide Cookies (Improves gated / age / bot-check content)
Two methods:

Method A (Upload):
1. Export browser cookies (Netscape format) using an extension like "Get cookies.txt".
2. Send the `cookies.txt` file to the bot.
3. Reply to that file with `/cookies`.

Method B (Persistent via ENV – good for Heroku):
1. Base64 encode your `cookies.txt` contents:
	```bash
	base64 -w0 cookies.txt > cookies.b64   # Linux/macOS
	# Windows (PowerShell):
	[Convert]::ToBase64String([IO.File]::ReadAllBytes('cookies.txt')) | Out-File cookies.b64 -NoNewline
	```
2. Set config var:
	```bash
	heroku config:set YTDLP_COOKIES_B64="<contents of cookies.b64>" -a <app>
	```
3. Deploy / restart; the bot decodes into `cookies/cookies.txt` automatically.

Log line confirms usage:
```
[ext] Using cookies file: cookies/cookies.txt
```

### 2. Audio Missing?
Causes & fixes:
| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| MP4 plays silently | ffmpeg missing | Install via Aptfile & rebuild |
| Repeated "Sign in" error | Cookies not provided / expired | Re-export fresh cookies |
| Still silent after recovery | Source video truly silent | None (expected) |

The downloader probes for audio; if absent it attempts a recovery download with a broader format.

### 3. Troubleshooting YouTube "Sign in to confirm you’re not a bot"
| Step | Action |
|------|--------|
| 1 | Confirm cookies file present or `YTDLP_COOKIES_B64` set |
| 2 | Check logs for `[ext] Using cookies file:` line |
| 3 | Test a non-shorts public video to isolate issue |
| 4 | Re-export cookies ensuring `__Secure-` and `SAPISID` entries present |
| 5 | Update yt-dlp (if needed) by bumping version in requirements.txt |

### 4. Supported Domains
Currently: YouTube, Instagram, Pinterest (pin.it short links). More can be added on request.

### 5. Commands Recap
| Command | Purpose |
|---------|---------|
| `/ext <url>` | Force external download (auto-detect also works) |
| `/cookies` | Register an uploaded cookies.txt (reply to file) |

---

## 🛡️ Security & Privacy

- **User Session**: Bot uses your user account to access restricted content
- **Data Handling**: Media is temporarily downloaded and immediately uploaded to target
- **Auto-Cleanup**: Downloaded files are automatically deleted after upload
- **Rate Limiting**: Built-in delays prevent hitting Telegram API limits
- **Error Logging**: Comprehensive logging for debugging (check `/logs`)

---

## 🔄 Automatic Channel Forwarding (Many → One)

Set up continuous monitoring of channels with automatic forwarding:

You can use environment variables for a baseline setup or configure everything at runtime with `/forward` commands.

Quick setup with commands:

```
/forward settarget @mytarget
/forward addsrc @source1,@source2,-1001234567890
/forward enable
```

Notes:
- The user session must be a member of all sources and have posting rights in the target.
- You can view and manage current settings any time with `/forward`.

The bot will automatically forward new posts from monitored channels while respecting the media-only filtering rules.

---

## 🚨 Troubleshooting

### Common Issues

**"Cannot access channel"**
- Ensure your user account is a member of the source channel
- For private channels, use channel ID instead of username
- Check if the channel exists and is accessible

**"No posting rights"**
- Verify you have permission to post in the target channel/group
- For channels: You need admin rights with "Post Messages" permission
- For groups: You need to be a member with posting rights

**"CHAT_FORWARDS_RESTRICTED"**
- Bot automatically handles this by using download+reupload method
- No action needed - the operation will continue seamlessly

**Rate Limiting**
- Bot has built-in delays and retry mechanisms
- Wait times are automatically handled
- Check `/logs` for detailed error information

---

## 📝 Notes

- **Media Only**: Channel cloning specifically filters out text, captions, and audio to create clean media-only channels
- **Protected Channels**: Bot can clone from channels that restrict forwarding by downloading and re-uploading content
- **Universal Targets**: Supports any chat type - public/private channels, groups, supergroups
- **Batch Operations**: Efficient handling of large channel cloning with progress tracking
- **Background Processing**: Long operations run in background with periodic status updates

---

## ☁️ Deploying to Heroku

### 1. Prerequisites
* Heroku account
* Heroku CLI installed: https://devcenter.heroku.com/articles/heroku-cli

### 2. Create App
```bash
heroku create my-telegram-media-bot
```

### 3. Buildpacks
Add (in order):
```bash
heroku buildpacks:add --index 1 heroku-community/apt
heroku buildpacks:add --index 2 heroku/python
```
The `Aptfile` ensures `ffmpeg` is installed for media conversion.

### 4. Set Config Vars
```bash
heroku config:set \
	API_ID=your_id \
	API_HASH=your_hash \
	BOT_TOKEN=123456:abcdef... \
	SESSION_STRING=your_session_string \
	SOURCE_CHANNELS=@ch1,@ch2 \
	DESTINATION_CHANNEL=@target \
	FORWARD_ENABLED=false
```

If you need to rotate secrets later, just run `heroku config:set` again.

### 5. Push Code
```bash
git push heroku main
```

### 6. Dyno Process Type
`Procfile` already defines:
```
worker: python main.py
```
Scale the worker dyno:
```bash
heroku ps:scale worker=1
```

### 7. Logs & Monitoring
```bash
heroku logs --tail
```
You should see "Bot Started!" and (if ffmpeg detected) conversion messages.

### 8. Updating
```bash
git pull origin main   # get local updates if forked
git push heroku main
```

### 9. Common Heroku Issues
| Symptom | Fix |
|---------|-----|
| App crashes immediately | Check config vars; missing BOT_TOKEN / SESSION_STRING |
| No audio in videos | Ensure Apt buildpack added and ffmpeg installed (Aptfile) |
| Memory quota exceeded | Reduce concurrency / dyno type; lower workers count |
| Slow startup | Use smaller dependency set; ensure no large file operations at boot |

### 10. Optional: Disable Cloning / Forwarding at Boot
Leave `FORWARD_ENABLED=false` until you confirm stability, then enable via `/forward enable` inside bot.

---

## 🤝 Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

---

## 📄 License

This project is for educational purposes only. Users are responsible for complying with Telegram's Terms of Service and respecting content creators' rights.

---

## 🔗 Repository

GitHub: [https://github.com/amnindersingh12/rdt](https://github.com/amnindersingh12/rdt)

---
