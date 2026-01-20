import os
import asyncio
from time import time
from types import SimpleNamespace
from typing import Optional, Dict, Callable, Union
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import (
    UsernameNotOccupied,
    PeerIdInvalid,
    ChannelPrivate,
    BadRequest,
    FloodWait,
)

from logger import LOGGER
from helpers.files import MAX_FILE_SIZE_BYTES, PREMIUM_MAX_FILE_SIZE_BYTES
from pyleaves import Leaves
from helpers.utils import progressArgs


class ChannelCloner:
    """
    Handles channel cloning operations with proper error handling.
    Supports downloading from protected/restricted channels.
    """

    def __init__(self, user_client: Client, bot_client: Client, *, delay: float = 1.0):
        self.user = user_client
        self.bot = bot_client
        self.delay = max(0.5, delay)

    async def get_channel_info(self, channel_identifier: str) -> Optional[Dict]:
        """Get information about a channel by username, ID, or t.me link."""
        ident: Union[str, int] = channel_identifier.strip()
        if isinstance(ident, str):
            if ident.startswith("https://t.me/+"):
                pass
            elif ident.startswith("@"):
                ident = ident[1:]
            elif ident.startswith("https://t.me/"):
                ident = ident.rstrip("/").rsplit("/", 1)[-1]
            
            try:
                if ident.replace("-", "").isdigit():
                    ident = int(ident)
            except Exception:
                pass

        try:
            chat = await self.user.get_chat(ident)
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
        progress_message: Optional[Message] = None,
    ) -> Dict[str, int]:
        """Clone messages from source channel to target channel."""
        src = self._normalize_channel_identifier(source_channel)
        dst = self._normalize_channel_identifier(target_channel)
        stats = {"successful": 0, "failed": 0, "skipped": 0, "total": 0}

        if start_id is None or end_id is None:
            LOGGER(__name__).info(f"Starting full channel clone from {src} to {dst}")
            async for msg in self.user.get_chat_history(src):
                current_id = getattr(msg, "id", None)
                if current_id is None:
                    stats["skipped"] += 1
                    continue

                ok = await self._copy_single_message(src, dst, current_id, progress_message)
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
        else:
            if start_id > end_id:
                raise ValueError("start_id cannot be greater than end_id")

            LOGGER(__name__).info(f"Starting range clone from {src} to {dst}: {start_id}-{end_id}")
            for mid in range(start_id, end_id + 1):
                ok = await self._copy_single_message(src, dst, mid, progress_message)
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

    def _normalize_channel_identifier(self, channel_str: str) -> Union[str, int]:
        """Normalize channel identifier by removing prefixes and extracting from URLs."""
        channel: Union[str, int] = channel_str.strip()
        if isinstance(channel, str):
            if channel.startswith("@"):
                channel = channel[1:]
            elif channel.startswith("https://t.me/") and not channel.startswith("https://t.me/+"):
                channel = channel.rstrip("/").rsplit("/", 1)[-1]
            
            if channel.replace("-", "").isdigit():
                try:
                    channel = int(channel)
                except Exception:
                    pass
        return channel

    async def _copy_single_message(
        self, 
        source_channel: Union[str, int], 
        target_channel: Union[str, int], 
        message_id: int, 
        progress_message: Optional[Message] = None,
        return_message_id: bool = False
    ) -> bool:
        """Copy a single message from source to target, handling protected content."""
        try:
            result = await self.user.get_messages(chat_id=source_channel, message_ids=message_id)
            
            if isinstance(result, list):
                source_msg = result[0] if result else None
            else:
                source_msg = result

            if not source_msg:
                return False

            # Skip audio and voice messages
            if source_msg.audio or source_msg.voice:
                LOGGER(__name__).info(f"Skipping audio/voice message {source_channel}/{message_id}")
                return None if return_message_id else False

            # BULLETPROOF Poll handling
            if getattr(source_msg, "poll", None):
                poll = source_msg.poll
                try:
                    # Safely get poll question
                    question = getattr(poll, "question", None)
                    if not question:
                        LOGGER(__name__).error(f"Poll has no question for {source_channel}/{message_id}")
                        return None if return_message_id else False
                    
                    poll_option_texts = []
                    options = getattr(poll, "options", [])
                    for option in options:
                        if isinstance(option, str):
                            poll_option_texts.append(option)
                        elif isinstance(option, dict) and "text" in option:
                            poll_option_texts.append(str(option["text"]))
                        elif hasattr(option, "text"):
                            poll_option_texts.append(str(option.text))
                        else:
                            poll_option_texts.append(str(option))
                    
                    if not poll_option_texts or len(poll_option_texts) < 2:
                        LOGGER(__name__).error(f"Invalid poll options for {source_channel}/{message_id}")
                        return None if return_message_id else False

                    # Safely get all poll attributes with defaults
                    poll_kwargs = {
                        "chat_id": target_channel,
                        "question": question,
                        "options": poll_option_texts,
                        "is_anonymous": getattr(poll, "is_anonymous", True),
                        "allows_multiple_answers": getattr(poll, "allows_multiple_answers", False),
                        "type": getattr(poll, "type", "regular"),
                        "correct_option_id": getattr(poll, "correct_option_id", None),
                        "explanation": getattr(poll, "explanation", None),
                        "open_period": getattr(poll, "open_period", None),
                        "close_date": getattr(poll, "close_date", None),
                        "is_closed": getattr(poll, "is_closed", False),
                    }
                    
                    try:
                        sent = await self.user.send_poll(**poll_kwargs)
                    except AttributeError as e:
                        if "has no attribute 'text'" in str(e):
                            poll_kwargs["options"] = [SimpleNamespace(text=t, entities=[]) for t in poll_option_texts]
                            sent = await self.user.send_poll(**poll_kwargs)
                        else:
                            raise

                    LOGGER(__name__).info(f"✅ Cloned poll '{question}' ({len(poll_option_texts)} options)")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception as poll_err:
                    LOGGER(__name__).error(
                        f"Failed to clone poll {source_channel}/{message_id}: {type(poll_err).__name__} - {poll_err}",
                        exc_info=True,
                    )
                    return None if return_message_id else False

            # Contacts
            if getattr(source_msg, "contact", None):
                try:
                    sent = await self.user.send_contact(
                        chat_id=target_channel,
                        phone_number=source_msg.contact.phone_number,
                        first_name=source_msg.contact.first_name,
                        last_name=getattr(source_msg.contact, "last_name", ""),
                        vcard=getattr(source_msg.contact, "vcard", None)
                    )
                    LOGGER(__name__).info(f"Copied contact {source_channel}/{message_id}")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception:
                    return None if return_message_id else False

            # Locations
            if getattr(source_msg, "location", None):
                try:
                    sent = await self.user.send_location(
                        chat_id=target_channel,
                        latitude=source_msg.location.latitude,
                        longitude=source_msg.location.longitude
                    )
                    LOGGER(__name__).info(f"Copied location {source_channel}/{message_id}")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception:
                    return None if return_message_id else False

            # Venues
            if getattr(source_msg, "venue", None):
                try:
                    sent = await self.user.send_venue(
                        chat_id=target_channel,
                        latitude=source_msg.venue.location.latitude,
                        longitude=source_msg.venue.location.longitude,
                        title=source_msg.venue.title,
                        address=source_msg.venue.address,
                        foursquare_id=getattr(source_msg.venue, "foursquare_id", None),
                        foursquare_type=getattr(source_msg.venue, "foursquare_type", None)
                    )
                    LOGGER(__name__).info(f"Copied venue {source_channel}/{message_id}")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception:
                    return None if return_message_id else False

            # Dice
            if getattr(source_msg, "dice", None):
                try:
                    sent = await self.user.send_dice(
                        chat_id=target_channel,
                        emoji=source_msg.dice.emoji
                    )
                    LOGGER(__name__).info(f"Copied dice {source_channel}/{message_id}")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception:
                    return None if return_message_id else False

            # Text-only messages
            if source_msg.text and not source_msg.media:
                try:
                    sent = await self.user.send_message(chat_id=target_channel, text=source_msg.text)
                    LOGGER(__name__).info(f"Copied text {source_channel}/{message_id}")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception:
                    return None if return_message_id else False

            # Caption-only messages
            if source_msg.caption and not source_msg.media:
                try:
                    sent = await self.user.send_message(chat_id=target_channel, text=source_msg.caption)
                    LOGGER(__name__).info(f"Copied caption {source_channel}/{message_id}")
                    if return_message_id:
                        return getattr(sent, "id", None)
                    return True
                except Exception:
                    return None if return_message_id else False

            # Media messages - ALWAYS download and reupload for protected channels
            if source_msg.media:
                LOGGER(__name__).info(f"Processing media message {source_channel}/{message_id}")
                return await self._download_and_reupload(
                    source_channel,
                    target_channel,
                    message_id,
                    progress_message,
                    return_message_id=return_message_id,
                )

            # Empty message
            LOGGER(__name__).info(f"Skipping empty message {source_channel}/{message_id}")
            return None if return_message_id else False

        except FloodWait as e:
            wait_s = int(getattr(e, "value", 1))
            LOGGER(__name__).warning(f"FloodWait {wait_s}s, sleeping...")
            await asyncio.sleep(wait_s + 1)
            try:
                return await self._copy_single_message(
                    source_channel,
                    target_channel,
                    message_id,
                    progress_message,
                    return_message_id=return_message_id,
                )
            except Exception:
                return None if return_message_id else False
        except Exception as e:
            LOGGER(__name__).error(f"Error copying {source_channel}/{message_id}: {type(e).__name__}")
            return None if return_message_id else False

    async def _download_and_reupload(
        self, 
        source_channel: Union[str, int], 
        target_channel: Union[str, int], 
        message_id: int, 
        progress_message: Optional[Message] = None,
        return_message_id: bool = False
    ) -> bool:
        """
        Download media from source and re-upload to target.
        Works even with protected/restricted content.
        """
        try:
            result = await self.user.get_messages(chat_id=source_channel, message_ids=message_id)
            
            if isinstance(result, list):
                source_msg = result[0] if result else None
            else:
                source_msg = result

            if not source_msg:
                LOGGER(__name__).warning(f"Message not found: {source_channel}/{message_id}")
                return None if return_message_id else False

            # Skip non-media messages
            if not source_msg.media:
                LOGGER(__name__).info(f"No media in {source_channel}/{message_id}")
                return None if return_message_id else False

            # Skip audio and voice
            if source_msg.audio or source_msg.voice:
                LOGGER(__name__).info(f"Skipping audio/voice {source_channel}/{message_id}")
                return None if return_message_id else False

            # Check file size limits
            try:
                me = await self.user.get_me()
                is_premium = bool(getattr(me, "is_premium", False))
            except Exception:
                is_premium = False

            limit = PREMIUM_MAX_FILE_SIZE_BYTES if is_premium else MAX_FILE_SIZE_BYTES
            size = None

            if source_msg.document:
                size = source_msg.document.file_size
            elif source_msg.video:
                size = source_msg.video.file_size

            if size is not None and size > limit:
                LOGGER(__name__).warning(f"File too large: {source_channel}/{message_id} ({size} bytes)")
                return None if return_message_id else False

            media_path = None
            converted_path = None
            start_ts = time()
            
            try:
                # Download the media (works even from protected channels with user session)
                LOGGER(__name__).info(f"📥 Downloading media {source_channel}/{message_id}...")
                if progress_message:
                    media_path = await source_msg.download(
                        progress=Leaves.progress_for_pyrogram,
                        progress_args=progressArgs("📥 Downloading", progress_message, start_ts),
                    )
                else:
                    media_path = await source_msg.download()

                if not media_path or not os.path.exists(media_path):
                    LOGGER(__name__).error(f"Download failed: {source_channel}/{message_id}")
                    return None if return_message_id else False

                LOGGER(__name__).info(f"Downloaded to: {media_path}")

                # Convert if needed
                from helpers.convert import ensure_mp4, ensure_png

                converted_path = media_path
                try:
                    if source_msg.video:
                        converted_path = await ensure_mp4(media_path)
                    elif source_msg.photo:
                        converted_path = await ensure_png(media_path)
                except Exception as conv_err:
                    LOGGER(__name__).warning(f"Conversion failed, using original: {conv_err}")
                    converted_path = media_path

                # Upload to target
                kwargs = {}
                if progress_message:
                    kwargs = {
                        "progress": Leaves.progress_for_pyrogram,
                        "progress_args": progressArgs("📤 Uploading", progress_message, start_ts),
                    }

                LOGGER(__name__).info(f"📤 Uploading to {target_channel}...")
                sent = None
                
                if source_msg.photo:
                    sent = await self.user.send_photo(chat_id=target_channel, photo=converted_path, **kwargs)
                elif source_msg.video:
                    sent = await self.user.send_video(chat_id=target_channel, video=converted_path, **kwargs)
                elif source_msg.document:
                    sent = await self.user.send_document(chat_id=target_channel, document=media_path, **kwargs)
                elif source_msg.sticker:
                    sent = await self.user.send_sticker(chat_id=target_channel, sticker=media_path)
                elif source_msg.video_note:
                    sent = await self.user.send_video_note(chat_id=target_channel, video_note=media_path)
                elif source_msg.animation:
                    sent = await self.user.send_animation(chat_id=target_channel, animation=media_path, **kwargs)
                else:
                    sent = await self.user.send_document(chat_id=target_channel, document=media_path, **kwargs)

                LOGGER(__name__).info(f"✅ Successfully cloned media {source_channel}/{message_id}")
                if return_message_id:
                    return getattr(sent, "id", None)
                return True

            except Exception as download_err:
                LOGGER(__name__).error(f"Download/upload error {source_channel}/{message_id}: {type(download_err).__name__} - {download_err}")
                return None if return_message_id else False
                
            finally:
                # Cleanup downloaded files
                try:
                    if media_path and os.path.exists(media_path):
                        os.remove(media_path)
                        LOGGER(__name__).debug(f"Cleaned up: {media_path}")
                except Exception as cleanup_err:
                    LOGGER(__name__).warning(f"Cleanup failed: {cleanup_err}")
                
                try:
                    if converted_path and converted_path != media_path and os.path.exists(converted_path):
                        os.remove(converted_path)
                except Exception:
                    pass

        except Exception as e:
            LOGGER(__name__).error(f"Fatal error in download/reupload {source_channel}/{message_id}: {type(e).__name__}")
            return None if return_message_id else False

