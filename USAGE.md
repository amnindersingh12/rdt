# Channel Cloning Bot - Quick Start Guide

## Setup Instructions

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Get Telegram Credentials**
   - Visit https://my.telegram.org/apps
   - Create a new application to get `API_ID` and `API_HASH`
   - Create a bot with @BotFather to get `BOT_TOKEN`
   - Generate a session string for your user account

3. **Configure the Bot**
   - Copy `config.env.example` to `config.env`
   - Fill in your actual credentials

4. **Run the Bot**
   ```bash
   python main.py
   ```

## Channel Cloning Usage

### Prerequisites
- Be a member of the source channel you want to clone
- Be an admin in the target channel with "Post Messages" permission

### Commands

**Clone entire channel:**
```
/clone_channel @sourcechannel @mytargetchannel
```

**Clone specific message range:**
```
/clone_range @sourcechannel @mytargetchannel 1 100
```

### Tips for Successful Cloning

1. **Start with small ranges** to test permissions and settings
2. **Monitor the process** - the bot provides real-time progress updates
3. **Handle large channels** - consider breaking them into smaller ranges
4. **Rate limits** - the bot automatically handles API rate limits
5. **Failed messages** - some messages may fail due to deletion or restrictions

### Troubleshooting

**"Cannot access source channel"**
- Ensure you're a member of the source channel
- Check if the channel username is correct

**"Cannot access target channel"**
- Ensure you're an admin in the target channel
- Verify you have "Post Messages" permission

**"FloodWait" errors**
- The bot automatically handles these by waiting
- Large channels may take several hours to clone completely

**Memory issues with large files**
- The bot downloads and immediately uploads files to save memory
- Very large files (>2GB for regular users, >4GB for premium) will be skipped

## Security Notes

- Never share your `config.env` file
- Your session string gives full access to your Telegram account
- Use a dedicated Telegram account for bot operations if possible
- Respect copyright and channel ownership when cloning content