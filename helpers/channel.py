import os
import asyncio
from typing import Optional, Dict, Callable
from pyrogram import Client
from pyrogram.errors import (
    UsernameNotOccupied,
    PeerIdInvalid,
    ChannelPrivate,
    BadRequest,
    FloodWait,
)
from logger import LOGGER


class ChannelCloner:
    """
    Handles channel cloning operations with proper error handling for username resolution
    and permission checking. Messages are copied (not forwarded) to remove the
    'Forwarded from' header, with fallback to download+reupload for protected channels.
    """

    def __init__(self, user_client: Client, bot_client: Client, *, delay: float = 1.0):
        self.user = user_client
        self.bot = bot_client
        self.delay = max(0.5, delay)  # Minimum 0.5s delay to avoid rate limits

    async def get_channel_info(self, channel_identifier: str) -> Optional[Dict]:
        """
        Get information about a channel by username, ID, or t.me link.
        Supports public/private channels and groups.
        
        Args:
            channel_identifier: Channel username, ID, t.me link, or private invite link
            
        Returns:
            Dict with channel info or None if channel can't be accessed
        """
        # Normalize identifier: allow '@name', 'name', t.me links, or private invite links
        ident = channel_identifier.strip()
        
        # Handle private invite links (like https://t.me/+ABC123...)
        if ident.startswith("https://t.me/+"):
            # Keep the full invite link for private groups/channels
            pass
        elif ident.startswith("@"):
            ident = ident[1:]
        elif ident.startswith("https://t.me/"):
            ident = ident.rstrip("/").rsplit("/", 1)[-1]
            
        try:
            chat = await self.user.get_chat(ident)
            
            # Determine chat type for better user feedback
            chat_type = str(chat.type)
            if hasattr(chat, 'type'):
                if 'channel' in chat_type.lower():
                    type_description = "Channel"
                elif 'group' in chat_type.lower() or 'supergroup' in chat_type.lower():
                    type_description = "Group"
                else:
                    type_description = chat_type.title()
            else:
                type_description = "Chat"
            
            return {
                "id": getattr(chat, "id", None),
                "title": getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(getattr(chat, "id", "Unknown")),
                "username": getattr(chat, "username", None),
                "type": chat_type,
                "type_description": type_description,
                "members_count": getattr(chat, "members_count", None),
                "is_private": not bool(getattr(chat, "username", None)),
            }
        except (UsernameNotOccupied, PeerIdInvalid, ChannelPrivate, BadRequest) as e:
            LOGGER(__name__).error(f"Cannot access channel '{channel_identifier}': {e}")
            return None
        except Exception as e:
            LOGGER(__name__).error(f"Unexpected error resolving '{channel_identifier}': {e}")
            return None

    async def clone_channel_messages(
        self,
        source_channel: str,
        target_channel: str,
        start_id: Optional[int] = None,
        end_id: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, int]:
        """
        Clone messages from source channel to target channel.
        
        Args:
            source_channel: Source channel username/ID
            target_channel: Target channel username/ID
            start_id: Start message ID (optional, will clone all if not provided)
            end_id: End message ID (optional)
            progress_callback: Function to call with progress updates
            
        Returns:
            Dict with statistics: successful, failed, skipped, total
        """
        # Normalize channel identifiers
        src = self._normalize_channel_identifier(source_channel)
        dst = self._normalize_channel_identifier(target_channel)

        stats = {"successful": 0, "failed": 0, "skipped": 0, "total": 0}

        # If no explicit range, iterate full history (newest to oldest)
        if start_id is None or end_id is None:
            LOGGER(__name__).info(f"Starting full channel clone from {src} to {dst}")
            async for msg in self.user.get_chat_history(src):
                current_id = getattr(msg, "id", None)
                if current_id is None:
                    stats["skipped"] += 1
                    continue
                    
                ok = await self._copy_single_message(src, dst, current_id)
                stats["total"] += 1
                if ok:
                    stats["successful"] += 1
                else:
                    stats["failed"] += 1
                    
                if progress_callback:
                    try:
                        await progress_callback(current_id, start_id or current_id, end_id or current_id, stats)
                    except Exception:
                        pass
                        
                await asyncio.sleep(self.delay)
            return stats

        # Ensure valid range
        if start_id > end_id:
            raise ValueError("start_id cannot be greater than end_id")

        # Clone by ID range
        LOGGER(__name__).info(f"Starting range clone from {src} to {dst}: {start_id}-{end_id}")
        for mid in range(start_id, end_id + 1):
            ok = await self._copy_single_message(src, dst, mid)
            stats["total"] += 1
            if ok:
                stats["successful"] += 1
            else:
                stats["failed"] += 1
                
            if progress_callback:
                try:
                    await progress_callback(mid, start_id, end_id, stats)
                except Exception:
                    pass
                    
            await asyncio.sleep(self.delay)

        return stats

    def _normalize_channel_identifier(self, channel_str: str) -> str:
        """Normalize channel identifier by removing prefixes and extracting from URLs."""
        channel = channel_str.strip()
        if channel.startswith("@"):
            channel = channel[1:]
        elif channel.startswith("https://t.me/"):
            channel = channel.rstrip("/").rsplit("/", 1)[-1]
        return channel

    async def _copy_single_message(self, source_channel: str, target_channel: str, message_id: int) -> bool:
        """
        Copy one message to the target to hide 'Forwarded from'.
        Falls back to download + re-upload for protected channels.
        Skips text-only messages and audio messages.
        """
        try:
            # First check if we should skip this message type
            result = await self.user.get_messages(chat_id=source_channel, message_ids=message_id)
            
            # Extract single message from result
            if isinstance(result, list):
                source_msg = result[0] if result else None
            else:
                source_msg = result
                
            if not source_msg:
                return False
                
            # Skip text-only messages
            if source_msg.text and not source_msg.media:
                LOGGER(__name__).info(f"Skipping text-only message {source_channel}/{message_id}")
                return False
                
            # Skip audio and voice messages
            if source_msg.audio or source_msg.voice:
                LOGGER(__name__).info(f"Skipping audio/voice message {source_channel}/{message_id}")
                return False
                
            # Skip caption-only messages (no media)
            if source_msg.caption and not source_msg.media:
                LOGGER(__name__).info(f"Skipping caption-only message {source_channel}/{message_id}")
                return False
            
            # Only proceed if it has media (excluding audio/voice)
            if not source_msg.media:
                LOGGER(__name__).info(f"Skipping message {source_channel}/{message_id} - no media content")
                return False
            
            await self.user.copy_message(
                chat_id=target_channel,
                from_chat_id=source_channel,
                message_id=message_id,
            )
            return True
        except FloodWait as e:
            wait_s = int(getattr(e, "value", 1))
            LOGGER(__name__).warning(f"FloodWait {wait_s}s on {source_channel}/{message_id}, sleeping...")
            await asyncio.sleep(wait_s + 1)
            try:
                await self.user.copy_message(
                    chat_id=target_channel,
                    from_chat_id=source_channel,
                    message_id=message_id,
                )
                return True
            except Exception as e2:
                LOGGER(__name__).error(f"Retry failed for {source_channel}/{message_id}: {e2}")
                return False
        except Exception as e:
            # Check if it's a forwarding restriction error (broader check)
            error_str = str(e).lower()
            if "chat_forwards_restricted" in error_str or "protected chat" in error_str or "can't forward" in error_str:
                LOGGER(__name__).info(f"Protected channel detected, using download+upload fallback for {source_channel}/{message_id}")
                return await self._download_and_reupload(source_channel, target_channel, message_id)
            
            LOGGER(__name__).error(f"Unexpected error copying {source_channel}/{message_id}: {e}")
            return False

    async def _download_and_reupload(self, source_channel: str, target_channel: str, message_id: int) -> bool:
        """
        Download media from source and re-upload to target channel.
        Used when copy/forward is restricted.
        Only forwards photos, videos, and documents. Skips audio and text.
        """
        try:
            # Get the source message
            result = await self.user.get_messages(chat_id=source_channel, message_ids=message_id)
            
            # Extract single message from result
            if isinstance(result, list):
                source_msg = result[0] if result else None
            else:
                source_msg = result
                
            if not source_msg:
                LOGGER(__name__).warning(f"Message {source_channel}/{message_id} not found")
                return False

            # Skip text-only messages
            if source_msg.text and not source_msg.media:
                LOGGER(__name__).info(f"Skipping text-only message {source_channel}/{message_id}")
                return False

            # Handle media messages - skip audio and voice messages
            if source_msg.media and not source_msg.audio and not source_msg.voice:
                media_path = None
                try:
                    # Download the media
                    media_path = await source_msg.download()
                    if not media_path:
                        LOGGER(__name__).error(f"Failed to download media from {source_channel}/{message_id}")
                        return False

                    # Re-upload based on media type WITHOUT any caption or text
                    if source_msg.photo:
                        await self.user.send_photo(
                            chat_id=target_channel,
                            photo=media_path
                        )
                    elif source_msg.video:
                        await self.user.send_video(
                            chat_id=target_channel,
                            video=media_path
                        )
                    elif source_msg.document:
                        await self.user.send_document(
                            chat_id=target_channel,
                            document=media_path
                        )
                    elif source_msg.sticker:
                        await self.user.send_sticker(
                            chat_id=target_channel,
                            sticker=media_path
                        )
                    elif source_msg.video_note:
                        await self.user.send_video_note(
                            chat_id=target_channel,
                            video_note=media_path
                        )
                    else:
                        # Generic document fallback WITHOUT caption
                        await self.user.send_document(
                            chat_id=target_channel,
                            document=media_path
                        )
                    
                    LOGGER(__name__).info(f"Successfully re-uploaded media {source_channel}/{message_id} (no text)")
                    return True
                    
                finally:
                    # Clean up downloaded file
                    try:
                        if media_path and os.path.exists(media_path):
                            os.remove(media_path)
                    except Exception as cleanup_error:
                        LOGGER(__name__).warning(f"Failed to cleanup {media_path}: {cleanup_error}")
            
            # Skip audio, voice, and caption-only messages
            elif source_msg.audio or source_msg.voice:
                LOGGER(__name__).info(f"Skipping audio/voice message {source_channel}/{message_id}")
                return False
            elif source_msg.caption and not source_msg.media:
                LOGGER(__name__).info(f"Skipping caption-only message {source_channel}/{message_id}")
                return False
                
            # Message has no content we want to transfer
            LOGGER(__name__).info(f"Skipping message {source_channel}/{message_id} - no transferable media")
            return False
            
        except Exception as e:
            LOGGER(__name__).error(f"Download+reupload failed for {source_channel}/{message_id}: {e}")
            return False