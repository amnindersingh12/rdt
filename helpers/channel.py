import asyncio
from typing import Optional, Dict, Callable
from pyrogram import Client
from pyrogram.errors import (
    UsernameNotOccupied, 
    PeerIdInvalid, 
    ChannelPrivate, 
    BadRequest,
    FloodWait
)
from logger import LOGGER


class ChannelCloner:
    """
    Handles channel cloning operations with proper error handling for username resolution
    and permission checking.
    """
    
    def __init__(self, user_client: Client, bot_client: Client):
        """
        Initialize the ChannelCloner with user and bot clients.
        
        Args:
            user_client: User client for accessing source channels
            bot_client: Bot client for posting to target channels
        """
        self.user = user_client
        self.bot = bot_client
    
    async def get_channel_info(self, channel_identifier: str) -> Optional[Dict]:
        """
        Get channel information by username or ID with proper error handling.
        
        Args:
            channel_identifier: Channel username (without @) or channel ID
            
        Returns:
            Dict with channel info if successful, None if channel not accessible
        """
        try:
            # Try to get channel info using user client
            chat = await self.user.get_chat(channel_identifier)
            return {
                'id': chat.id,
                'title': chat.title,
                'username': chat.username,
                'type': chat.type.name
            }
        except UsernameNotOccupied:
            LOGGER(__name__).error(f"Username '{channel_identifier}' is not occupied by anyone")
            return None
        except (PeerIdInvalid, ChannelPrivate):
            LOGGER(__name__).error(f"Cannot access channel '{channel_identifier}' - private or invalid")
            return None
        except BadRequest as e:
            LOGGER(__name__).error(f"Bad request when accessing '{channel_identifier}': {e}")
            return None
        except Exception as e:
            LOGGER(__name__).error(f"Unexpected error accessing '{channel_identifier}': {e}")
            return None
    
    async def clone_channel_messages(
        self, 
        source_channel: str, 
        target_channel: str,
        start_id: Optional[int] = None,
        end_id: Optional[int] = None,
        progress_callback: Optional[Callable] = None
    ) -> Dict[str, int]:
        """
        Clone messages from source channel to target channel.
        
        Args:
            source_channel: Source channel username or ID
            target_channel: Target channel username or ID  
            start_id: Starting message ID (optional, defaults to 1)
            end_id: Ending message ID (optional, defaults to latest)
            progress_callback: Callback function for progress updates
            
        Returns:
            Dict with statistics: {'successful': int, 'failed': int, 'skipped': int, 'total': int}
        """
        stats = {'successful': 0, 'failed': 0, 'skipped': 0, 'total': 0}
        
        try:
            # Validate source channel access
            source_info = await self.get_channel_info(source_channel)
            if not source_info:
                raise ValueError(f"Cannot access source channel: {source_channel}")
            
            # Validate target channel access - use user client for validation
            target_info = await self.get_channel_info(target_channel)
            if not target_info:
                raise ValueError(f"Cannot access target channel: {target_channel}")
            
            # Determine message range
            if start_id is None:
                start_id = 1
            
            if end_id is None:
                # Get the latest message ID from source channel
                try:
                    async for message in self.user.get_chat_history(source_channel, limit=1):
                        end_id = message.id
                        break
                    else:
                        end_id = start_id  # No messages found
                except Exception as e:
                    LOGGER(__name__).error(f"Failed to get latest message ID: {e}")
                    end_id = start_id
            
            LOGGER(__name__).info(f"Cloning messages {start_id}-{end_id} from {source_channel} to {target_channel}")
            
            # Clone messages in the specified range
            for current_id in range(start_id, end_id + 1):
                try:
                    # Get message from source
                    message = await self.user.get_messages(source_channel, current_id)
                    
                    if not message or message.empty:
                        stats['skipped'] += 1
                        stats['total'] += 1
                        continue
                    
                    # Skip service messages and deleted messages
                    if message.service or not (message.text or message.caption or message.media):
                        stats['skipped'] += 1
                        stats['total'] += 1
                        continue
                    
                    # Clone the message to target channel
                    await self._clone_single_message(message, target_channel)
                    stats['successful'] += 1
                    
                    # Rate limiting to avoid flood
                    await asyncio.sleep(1)
                    
                except FloodWait as e:
                    LOGGER(__name__).warning(f"Flood wait for {e.value} seconds")
                    await asyncio.sleep(e.value)
                    continue
                except Exception as e:
                    LOGGER(__name__).error(f"Error processing message {current_id}: {e}")
                    stats['failed'] += 1
                
                stats['total'] += 1
                
                # Call progress callback if provided
                if progress_callback and current_id % 10 == 0:
                    try:
                        await progress_callback(current_id, start_id, end_id, stats.copy())
                    except Exception as callback_error:
                        LOGGER(__name__).warning(f"Progress callback error: {callback_error}")
            
            LOGGER(__name__).info(f"Cloning completed. Stats: {stats}")
            return stats
            
        except Exception as e:
            LOGGER(__name__).error(f"Channel cloning failed: {e}")
            raise
    
    async def _clone_single_message(self, message, target_channel: str):
        """
        Clone a single message to the target channel.
        
        Args:
            message: Source message object
            target_channel: Target channel identifier
        """
        try:
            if message.media_group_id:
                # Handle media groups - get all messages in the group
                media_group = await message.get_media_group()
                if media_group:
                    # Forward the first message in the group (which forwards the entire group)
                    await media_group[0].forward(target_channel)
            elif message.media:
                # Forward media messages
                await message.forward(target_channel)
            elif message.text:
                # Send text messages
                await self.bot.send_message(target_channel, message.text.markdown)
            
        except Exception as e:
            LOGGER(__name__).error(f"Failed to clone message {message.id}: {e}")
            raise