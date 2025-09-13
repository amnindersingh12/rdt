import asyncio
import time
from typing import Optional, Tuple, Dict, Any
from pyrogram import Client
from pyrogram.errors import PeerIdInvalid, BadRequest, FloodWait, ChatAdminRequired
from pyrogram.types import Message

from logger import LOGGER
from helpers.utils import processMediaGroup, send_media
from helpers.msg import get_parsed_msg, get_file_name
from helpers.files import get_download_path, cleanup_download


class ChannelCloner:
    """Handles cloning of Telegram channels with proper error handling and rate limiting."""
    
    def __init__(self, user_client: Client, bot_client: Client):
        self.user = user_client
        self.bot = bot_client
        self.stats = {
            'total': 0,
            'successful': 0,
            'failed': 0,
            'skipped': 0
        }
    
    async def get_channel_info(self, channel_identifier: str) -> Optional[Dict[str, Any]]:
        """
        Get channel information and validate access.
        
        Args:
            channel_identifier: Channel username (without @) or channel ID
            
        Returns:
            Channel info dict or None if inaccessible
        """
        try:
            # Try to get channel information
            if channel_identifier.startswith('@'):
                channel_identifier = channel_identifier[1:]
            
            chat = await self.user.get_chat(channel_identifier)
            return {
                'id': chat.id,
                'title': chat.title,
                'username': chat.username,
                'members_count': getattr(chat, 'members_count', 0),
                'type': str(chat.type)
            }
        except (PeerIdInvalid, BadRequest) as e:
            LOGGER(__name__).error(f"Cannot access channel {channel_identifier}: {e}")
            return None
    
    async def clone_channel_messages(
        self, 
        source_channel: str, 
        target_channel: str,
        start_id: Optional[int] = None,
        end_id: Optional[int] = None,
        progress_callback=None
    ) -> Dict[str, int]:
        """
        Clone messages from source channel to target channel.
        
        Args:
            source_channel: Source channel username or ID
            target_channel: Target channel username or ID
            start_id: Starting message ID (optional)
            end_id: Ending message ID (optional)
            progress_callback: Function to call with progress updates
            
        Returns:
            Dictionary with cloning statistics
        """
        # Reset stats
        self.stats = {'total': 0, 'successful': 0, 'failed': 0, 'skipped': 0}
        
        # Validate source channel access
        source_info = await self.get_channel_info(source_channel)
        if not source_info:
            raise ValueError(f"Cannot access source channel: {source_channel}")
        
        # Validate target channel access and admin rights
        target_info = await self.get_channel_info(target_channel)
        if not target_info:
            raise ValueError(f"Cannot access target channel: {target_channel}")
        
        try:
            # Check if user has admin rights in target channel
            member = await self.user.get_chat_member(target_channel, "me")
            if not member.privileges or not member.privileges.can_post_messages:
                raise ChatAdminRequired("User must be admin with post message rights in target channel")
        except Exception as e:
            raise ValueError(f"Cannot verify admin rights in target channel: {e}")
        
        LOGGER(__name__).info(f"Starting channel clone: {source_info['title']} -> {target_info['title']}")
        
        # Get message range if not specified
        if not start_id or not end_id:
            try:
                # Get some recent messages to determine range
                async for message in self.user.get_chat_history(source_channel, limit=1):
                    if not end_id:
                        end_id = message.id
                    break
                
                if not start_id:
                    start_id = 1
                    
            except Exception as e:
                raise ValueError(f"Cannot determine message range: {e}")
        
        LOGGER(__name__).info(f"Cloning messages {start_id} to {end_id}")
        
        # Clone messages in the specified range
        for msg_id in range(start_id, end_id + 1):
            try:
                # Get message from source
                message = await self.user.get_messages(source_channel, msg_id)
                
                if not message or message.empty:
                    self.stats['skipped'] += 1
                    continue
                
                self.stats['total'] += 1
                
                # Progress update
                if progress_callback:
                    await progress_callback(msg_id, start_id, end_id, self.stats)
                
                # Clone the message
                success = await self._clone_single_message(message, target_channel)
                if success:
                    self.stats['successful'] += 1
                else:
                    self.stats['failed'] += 1
                
                # Rate limiting - wait between messages
                await asyncio.sleep(2)
                
            except FloodWait as e:
                LOGGER(__name__).warning(f"FloodWait: sleeping for {e.value} seconds")
                await asyncio.sleep(e.value)
                continue
            except Exception as e:
                LOGGER(__name__).error(f"Error processing message {msg_id}: {e}")
                self.stats['failed'] += 1
                continue
        
        return self.stats
    
    async def _clone_single_message(self, message: Message, target_channel: str) -> bool:
        """
        Clone a single message to the target channel.
        
        Args:
            message: Source message to clone
            target_channel: Target channel identifier
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Handle different message types
            if message.media_group_id:
                return await self._clone_media_group(message, target_channel)
            elif message.media:
                return await self._clone_media_message(message, target_channel)
            elif message.text:
                return await self._clone_text_message(message, target_channel)
            else:
                # Skip service messages, deleted messages, etc.
                return False
                
        except Exception as e:
            LOGGER(__name__).error(f"Error cloning message {message.id}: {e}")
            return False
    
    async def _clone_text_message(self, message: Message, target_channel: str) -> bool:
        """Clone a text-only message."""
        try:
            parsed_text = await get_parsed_msg(message.text or "", message.entities)
            
            await self.user.send_message(
                chat_id=target_channel,
                text=parsed_text,
                disable_web_page_preview=message.disable_web_page_preview
            )
            return True
        except Exception as e:
            LOGGER(__name__).error(f"Error cloning text message: {e}")
            return False
    
    async def _clone_media_message(self, message: Message, target_channel: str) -> bool:
        """Clone a single media message."""
        try:
            # Download the media first
            filename = get_file_name(message.id, message)
            download_path = get_download_path(f"clone_{target_channel}", filename)
            
            media_path = await message.download(file_name=download_path)
            
            # Parse caption
            parsed_caption = await get_parsed_msg(
                message.caption or "", message.caption_entities
            )
            
            # Send media based on type
            if message.photo:
                await self.user.send_photo(
                    chat_id=target_channel,
                    photo=media_path,
                    caption=parsed_caption
                )
            elif message.video:
                await self.user.send_video(
                    chat_id=target_channel,
                    video=media_path,
                    caption=parsed_caption,
                    duration=getattr(message.video, 'duration', 0),
                    width=getattr(message.video, 'width', 0),
                    height=getattr(message.video, 'height', 0)
                )
            elif message.document:
                await self.user.send_document(
                    chat_id=target_channel,
                    document=media_path,
                    caption=parsed_caption
                )
            elif message.audio:
                await self.user.send_audio(
                    chat_id=target_channel,
                    audio=media_path,
                    caption=parsed_caption,
                    duration=getattr(message.audio, 'duration', 0),
                    performer=getattr(message.audio, 'performer', None),
                    title=getattr(message.audio, 'title', None)
                )
            elif message.voice:
                await self.user.send_voice(
                    chat_id=target_channel,
                    voice=media_path,
                    caption=parsed_caption,
                    duration=getattr(message.voice, 'duration', 0)
                )
            elif message.video_note:
                await self.user.send_video_note(
                    chat_id=target_channel,
                    video_note=media_path,
                    duration=getattr(message.video_note, 'duration', 0)
                )
            elif message.animation:
                await self.user.send_animation(
                    chat_id=target_channel,
                    animation=media_path,
                    caption=parsed_caption
                )
            elif message.sticker:
                await self.user.send_sticker(
                    chat_id=target_channel,
                    sticker=media_path
                )
            
            # Cleanup downloaded file
            cleanup_download(media_path)
            return True
            
        except Exception as e:
            LOGGER(__name__).error(f"Error cloning media message: {e}")
            if 'media_path' in locals():
                cleanup_download(media_path)
            return False
    
    async def _clone_media_group(self, message: Message, target_channel: str) -> bool:
        """Clone a media group (album)."""
        try:
            # Get all messages in the media group
            media_group_messages = await message.get_media_group()
            
            if not media_group_messages:
                return False
            
            # Download and prepare all media
            media_files = []
            temp_paths = []
            
            for msg in media_group_messages:
                try:
                    filename = get_file_name(msg.id, msg)
                    download_path = get_download_path(f"clone_{target_channel}", filename)
                    
                    media_path = await msg.download(file_name=download_path)
                    temp_paths.append(media_path)
                    
                    parsed_caption = await get_parsed_msg(
                        msg.caption or "", msg.caption_entities
                    )
                    
                    if msg.photo:
                        media_files.append({
                            'type': 'photo',
                            'path': media_path,
                            'caption': parsed_caption
                        })
                    elif msg.video:
                        media_files.append({
                            'type': 'video',
                            'path': media_path,
                            'caption': parsed_caption,
                            'duration': getattr(msg.video, 'duration', 0),
                            'width': getattr(msg.video, 'width', 0),
                            'height': getattr(msg.video, 'height', 0)
                        })
                    elif msg.document:
                        media_files.append({
                            'type': 'document',
                            'path': media_path,
                            'caption': parsed_caption
                        })
                    elif msg.audio:
                        media_files.append({
                            'type': 'audio',
                            'path': media_path,
                            'caption': parsed_caption,
                            'duration': getattr(msg.audio, 'duration', 0)
                        })
                        
                except Exception as e:
                    LOGGER(__name__).error(f"Error downloading media group item: {e}")
                    continue
            
            # Send media group if we have valid media files
            if media_files:
                # For simplicity, send media individually with small delay
                # This ensures better compatibility and error handling
                for media in media_files:
                    try:
                        if media['type'] == 'photo':
                            await self.user.send_photo(
                                chat_id=target_channel,
                                photo=media['path'],
                                caption=media['caption']
                            )
                        elif media['type'] == 'video':
                            await self.user.send_video(
                                chat_id=target_channel,
                                video=media['path'],
                                caption=media['caption'],
                                duration=media.get('duration', 0),
                                width=media.get('width', 0),
                                height=media.get('height', 0)
                            )
                        elif media['type'] == 'document':
                            await self.user.send_document(
                                chat_id=target_channel,
                                document=media['path'],
                                caption=media['caption']
                            )
                        elif media['type'] == 'audio':
                            await self.user.send_audio(
                                chat_id=target_channel,
                                audio=media['path'],
                                caption=media['caption'],
                                duration=media.get('duration', 0)
                            )
                        
                        await asyncio.sleep(1)  # Small delay between media items
                        
                    except Exception as e:
                        LOGGER(__name__).error(f"Error sending media group item: {e}")
                        continue
            
            # Cleanup all downloaded files
            for path in temp_paths:
                cleanup_download(path)
            
            return len(media_files) > 0
            
        except Exception as e:
            LOGGER(__name__).error(f"Error cloning media group: {e}")
            # Cleanup on error
            if 'temp_paths' in locals():
                for path in temp_paths:
                    cleanup_download(path)
            return False