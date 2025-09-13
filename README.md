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

### Channel Forwarding
- `/forward` - Show current forwarding settings  
- `/forward status` - Display forwarding configuration
- `/forward help` - Setup instructions for auto-forwarding

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

# Clone to private group
/clone_channel cd https://t.me/+ABC123DEF

# Clone specific message range  
/clone_range @source @target 100 200

# Clone from protected channel
/clone_range cd cds 8400 8500
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

---

## 🛡️ Security & Privacy

- **User Session**: Bot uses your user account to access restricted content
- **Data Handling**: Media is temporarily downloaded and immediately uploaded to target
- **Auto-Cleanup**: Downloaded files are automatically deleted after upload
- **Rate Limiting**: Built-in delays prevent hitting Telegram API limits
- **Error Logging**: Comprehensive logging for debugging (check `/logs`)

---

## 🔄 Automatic Channel Forwarding

Set up continuous monitoring of channels with automatic forwarding:

1. **Configure** source and destination channels in `config.env`
2. **Enable** forwarding with `FORWARD_ENABLED=true`
3. **Monitor** with `/forward status` command
4. **Manage** with `/forward help` for detailed setup

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

## 🤝 Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

---

## 📄 License

This project is for educational purposes only. Users are responsible for complying with Telegram's Terms of Service and respecting content creators' rights.

---

## 🔗 Repository

GitHub: [https://github.com/amnindersingh12/rdt](https://github.com/amnindersingh12/rdt)

---
