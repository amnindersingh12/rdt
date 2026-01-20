"""
Replication Manager - Real-time Channel Cloning with Full Message Support

This module provides:
1. Real-time monitoring of source channels for new messages
2. Initial backfill to catch up with existing messages
3. Reply preservation - maintains reply relationships
4. Full message type support (text, media, polls, documents, etc.)
5. Deduplication - tracks what's already been cloned
6. Multiple source-target channel mappings
7. Heroku-compatible (lightweight, uses SQLite for persistence)
"""

import os
import sqlite3
import asyncio
from typing import Optional, List, Dict, Union, Tuple
from time import time

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, BadRequest, PeerIdInvalid
from types import SimpleNamespace

from logger import LOGGER
from helpers.config_store import load_config, save_config


class ReplicationStore:
    """SQLite-based storage for tracking cloned messages and reply mappings."""
    
    def __init__(self, path: str = "replication.sqlite") -> None:
        self.path = path
        self._init_db()
    
    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        return sqlite3.connect(self.path, timeout=30)
    
    def _init_db(self) -> None:
        with self._connect() as con:
            # Track cloned messages (source -> target mapping)
            con.execute("""
                CREATE TABLE IF NOT EXISTS message_map (
                    source_chat INTEGER NOT NULL,
                    source_msg INTEGER NOT NULL,
                    target_chat INTEGER NOT NULL,
                    target_msg INTEGER NOT NULL,
                    cloned_at REAL NOT NULL,
                    PRIMARY KEY(source_chat, source_msg, target_chat)
                )
            """)
            # Track last processed message ID per source-target pair
            con.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    source_chat INTEGER NOT NULL,
                    target_chat INTEGER NOT NULL,
                    last_msg_id INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(source_chat, target_chat)
                )
            """)
            # Add index for faster lookups
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_message_map_source 
                ON message_map(source_chat, source_msg)
            """)
            con.commit()
    
    def set_mapping(self, source_chat: int, source_msg: int, target_chat: int, target_msg: int) -> None:
        """Store source->target message mapping."""
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO message_map(source_chat, source_msg, target_chat, target_msg, cloned_at) VALUES(?,?,?,?,?)",
                (int(source_chat), int(source_msg), int(target_chat), int(target_msg), time()),
            )
            con.commit()
    
    def get_target_msg_id(self, source_chat: int, source_msg: int, target_chat: int) -> Optional[int]:
        """Get the target message ID for a source message."""
        with self._connect() as con:
            cur = con.execute(
                "SELECT target_msg FROM message_map WHERE source_chat=? AND source_msg=? AND target_chat=?",
                (int(source_chat), int(source_msg), int(target_chat)),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None
    
    def is_cloned(self, source_chat: int, source_msg: int, target_chat: int) -> bool:
        """Check if a message has already been cloned."""
        return self.get_target_msg_id(source_chat, source_msg, target_chat) is not None
    
    def get_last_synced_id(self, source_chat: int, target_chat: int) -> int:
        """Get the last synced message ID for a source-target pair."""
        with self._connect() as con:
            cur = con.execute(
                "SELECT last_msg_id FROM sync_state WHERE source_chat=? AND target_chat=?",
                (int(source_chat), int(target_chat)),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    
    def set_last_synced_id(self, source_chat: int, target_chat: int, msg_id: int) -> None:
        """Update the last synced message ID for a source-target pair."""
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO sync_state(source_chat, target_chat, last_msg_id) VALUES(?,?,?)",
                (int(source_chat), int(target_chat), int(msg_id)),
            )
            con.commit()
    
    def get_stats(self, source_chat: int, target_chat: int) -> Dict:
        """Get statistics for a source-target pair."""
        with self._connect() as con:
            cur = con.execute(
                "SELECT COUNT(*), MAX(cloned_at) FROM message_map WHERE source_chat=? AND target_chat=?",
                (int(source_chat), int(target_chat)),
            )
            row = cur.fetchone()
            count = int(row[0]) if row and row[0] else 0
            last_cloned = float(row[1]) if row and row[1] else 0
            
            cur2 = con.execute(
                "SELECT last_msg_id FROM sync_state WHERE source_chat=? AND target_chat=?",
                (int(source_chat), int(target_chat)),
            )
            row2 = cur2.fetchone()
            last_synced = int(row2[0]) if row2 else 0
            
            return {
                "cloned_count": count,
                "last_cloned_at": last_cloned,
                "last_synced_id": last_synced,
            }


class ReplicationManager:
    """
    Manages real-time channel replication with full message support.
    
    Features:
    - Real-time monitoring for new messages
    - Initial backfill to catch up
    - Reply preservation
    - Full message type support
    - Deduplication
    """
    
    def __init__(self, user: Client) -> None:
        self.user = user
        self.store = ReplicationStore()
        self.running = False
        self.backfill_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._media_group_cache: Dict[str, bool] = {}  # Track processed media groups
    
    def get_mappings(self) -> List[Dict]:
        """Get all source-target channel mappings from config."""
        cfg = load_config()
        return cfg.get("replication_mappings", [])
    
    def set_mappings(self, mappings: List[Dict]) -> None:
        """Save source-target channel mappings to config."""
        cfg = load_config()
        cfg["replication_mappings"] = mappings
        save_config(cfg)
    
    def add_mapping(self, source_chat: int, target_chat: int) -> None:
        """Add a new source-target mapping."""
        mappings = self.get_mappings()
        
        # Check if mapping already exists
        for m in mappings:
            if m.get("source") == source_chat and m.get("target") == target_chat:
                return  # Already exists
        
        mappings.append({
            "source": source_chat,
            "target": target_chat,
            "enabled": True,
        })
        self.set_mappings(mappings)
    
    def remove_mapping(self, source_chat: int, target_chat: int) -> bool:
        """Remove a source-target mapping."""
        mappings = self.get_mappings()
        new_mappings = [
            m for m in mappings 
            if not (m.get("source") == source_chat and m.get("target") == target_chat)
        ]
        if len(new_mappings) != len(mappings):
            self.set_mappings(new_mappings)
            return True
        return False
    
    def is_enabled(self) -> bool:
        """Check if replication is globally enabled."""
        cfg = load_config()
        return cfg.get("replication_enabled", False)
    
    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable replication globally."""
        cfg = load_config()
        cfg["replication_enabled"] = enabled
        save_config(cfg)
    
    def get_targets_for_source(self, source_chat: int) -> List[int]:
        """Get all target channels for a given source channel."""
        if not self.is_enabled():
            return []
        
        mappings = self.get_mappings()
        targets = []
        for m in mappings:
            if m.get("enabled", True) and m.get("source") == source_chat:
                targets.append(m.get("target"))
        return targets
    
    async def _resolve_chat_id(self, identifier: Union[str, int]) -> Optional[int]:
        """Resolve a channel identifier to a chat ID."""
        try:
            if isinstance(identifier, int):
                return identifier
            
            ident = str(identifier).strip()
            
            # Handle t.me invite links
            if ident.startswith("https://t.me/+"):
                chat = await self.user.join_chat(ident)
                return chat.id
            
            # Handle t.me usernames
            if ident.startswith("https://t.me/"):
                ident = ident.rstrip("/").rsplit("/", 1)[-1]
            
            if ident.startswith("@"):
                ident = ident[1:]
            
            # Try as numeric ID
            if ident.replace("-", "").isdigit():
                return int(ident)
            
            # Try to get chat by username
            chat = await self.user.get_chat(ident)
        except Exception as e:
            LOGGER(__name__).error(f"Failed to resolve chat ID for {identifier}: {e}")
            return None

    async def _bypass_restriction(self, source_chat: int, target_chat: int, message: Message, caption: str, reply_to: Optional[int]) -> Optional[int]:
        """Bypass copyright/forward restrictions by downloading and re-uploading."""
        path = None
        try:
            LOGGER(__name__).info(f"Bypassing restriction for {source_chat}/{message.id} (downloading...)")
            
            # Download
            # Use a unique path to avoid collisions
            file_name = f"downloads/{source_chat}_{message.id}"
            path = await self.user.download_media(message, file_name=file_name)
            
            if not path:
                return None
                
            sent = None
            entities = message.caption_entities if message.caption else message.entities
            
            # Re-upload based on type
            if getattr(message, "photo", None):
                sent = await self.user.send_photo(target_chat, path, caption=caption, caption_entities=entities, reply_to_message_id=reply_to)
            elif getattr(message, "video", None):
                sent = await self.user.send_video(target_chat, path, caption=caption, caption_entities=entities, duration=message.video.duration, reply_to_message_id=reply_to)
            elif getattr(message, "document", None):
                sent = await self.user.send_document(target_chat, path, caption=caption, caption_entities=entities, reply_to_message_id=reply_to)
            elif getattr(message, "audio", None):
                sent = await self.user.send_audio(target_chat, path, caption=caption, caption_entities=entities, duration=message.audio.duration, reply_to_message_id=reply_to)
            elif getattr(message, "voice", None):
                sent = await self.user.send_voice(target_chat, path, caption=caption, caption_entities=entities, duration=message.voice.duration, reply_to_message_id=reply_to)
            elif getattr(message, "video_note", None):
                sent = await self.user.send_video_note(target_chat, path, reply_to_message_id=reply_to)
            elif getattr(message, "animation", None):
                sent = await self.user.send_animation(target_chat, path, caption=caption, caption_entities=entities, reply_to_message_id=reply_to)
            elif getattr(message, "sticker", None):
                sent = await self.user.send_sticker(target_chat, path, reply_to_message_id=reply_to)
            
            if sent:
                LOGGER(__name__).info(f"Bypass successful: {source_chat}/{message.id} -> {target_chat}/{sent.id}")
                return sent.id
                
        except Exception as e:
            LOGGER(__name__).error(f"Bypass failed for {source_chat}/{message.id}: {e}")
        finally:
            # Cleanup
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
        return None
    def _rewrite_links(self, text: str, source_chat: int, target_chat: int) -> str:
        """Rewrite links in text that point to source channel to point to target channel."""
        if not text:
            return text
        
        # Helper to replace numeric ID links
        # Link format: https://t.me/c/1234567890/123
        # Source ID in link is usually without -100 prefix, just the 10-digit number
        src_id_str = str(source_chat).replace("-100", "").replace("-", "")
        tgt_id_str = str(target_chat).replace("-100", "").replace("-", "")
        
        # Simple string replacement for private channel links
        # This is a basic heuristic; regex would be more robust but this covers standard clients.
        if f"/c/{src_id_str}/" in text:
            # We need to find specific message IDs to map
            import re
            # Pattern: t.me/c/1234567890/54321
            pattern = re.compile(rf"(t\.me/c/{src_id_str}/(\d+))")
            
            def replace_match(match):
                original_url = match.group(1)
                msg_id = int(match.group(2))
                # Try to find mapped ID
                mapped_id = self.store.get_target_msg_id(source_chat, msg_id, target_chat)
                if mapped_id:
                    return f"t.me/c/{tgt_id_str}/{mapped_id}"
                return original_url # Keep original if not cloned yet
            
            try:
                text = pattern.sub(replace_match, text)
            except Exception:
                pass
            
        # Handle Public Username Links if applicable
        # (Assuming we might know usernames, but for now mostly ID based)
        return text
    async def _copy_message(
        self, 
        source_chat: int, 
        target_chat: int, 
        message: Message,
        retry_count: int = 0
    ) -> Optional[int]:
        """
        Copy a single message from source to target with full type support.
        Returns the target message ID if successful.
        """
        try:
            # Skip service messages
            if message.service:
                return None
            
            # Check if already cloned
            if self.store.is_cloned(source_chat, message.id, target_chat):
                return self.store.get_target_msg_id(source_chat, message.id, target_chat)
            
            # Determine reply_to_message_id
            reply_to_msg_id = None
            if message.reply_to_message_id:
                reply_to_msg_id = self.store.get_target_msg_id(
                    source_chat, 
                    message.reply_to_message_id, 
                    target_chat
                )
            
            # Prepare Caption/Text with link rewriting
            caption = message.caption or ""
            text = message.text or ""
            
            if caption:
                caption = self._rewrite_links(caption, source_chat, target_chat)
            if text:
                text = self._rewrite_links(text, source_chat, target_chat)
                
            entities = message.caption_entities if message.caption else message.entities
            
            sent = None
            
            # 1. MEDIA GROUP (Handle strict first)
            if message.media_group_id:
                mg_key = f"{source_chat}_{message.media_group_id}_{target_chat}"
                if mg_key in self._media_group_cache:
                    return None
                try:
                    msgs = await self.user.get_media_group(source_chat, message.id)
                    if msgs:
                        first_msg = min(msgs, key=lambda m: m.id)
                        if message.id == first_msg.id:
                            self._media_group_cache[mg_key] = True
                            # IMPORTANT: copy_media_group doesn't easily allow caption rewriting
                            # We accept we might lose link rewriting here for now to keep media group structure
                            sent_msgs = await self.user.copy_media_group(
                                chat_id=target_chat,
                                from_chat_id=source_chat,
                                message_id=message.id,
                                reply_to_message_id=reply_to_msg_id,
                            )
                            if sent_msgs:
                                for src_m, tgt_m in zip(msgs, sent_msgs):
                                    self.store.set_mapping(source_chat, src_m.id, target_chat, tgt_m.id)
                                return sent_msgs[0].id
                        else:
                            return None
                except Exception:
                    pass # Fallback to single media
            
            # 2. POLL
            if getattr(message, "poll", None):
                poll = message.poll
                if poll.question and len(poll.options) >= 2:
                    poll_options = []
                    for opt in poll.options:
                        text_val = opt.text if hasattr(opt, "text") else str(opt)
                        poll_options.append(text_val)
                        
                    # Fix QuizCorrectAnswersEmpty: Only send correct_option_id if type is QUIZ
                    correct_id = poll.correct_option_id if poll.type == "quiz" else None
                    
                    try:
                        sent = await self.user.send_poll(
                            chat_id=target_chat,
                            question=poll.question,
                            options=poll_options,
                            is_anonymous=poll.is_anonymous,
                            allows_multiple_answers=poll.allows_multiple_answers,
                            type=poll.type,
                            correct_option_id=correct_id,
                            explanation=poll.explanation,
                            reply_to_message_id=reply_to_msg_id,
                        )
                    except Exception:
                        pass

            # 3. TEXT
            elif text:
                 # Check for webpage preview
                 disable_preview = True
                 if message.web_page:
                     disable_preview = False
                     
                 sent = await self.user.send_message(
                     chat_id=target_chat,
                     text=text,
                     entities=entities,
                     disable_web_page_preview=disable_preview,
                     reply_to_message_id=reply_to_msg_id,
                 )

            # 4. DOCUMENT
            elif getattr(message, "document", None):
                sent = await self.user.send_document(
                    chat_id=target_chat,
                    document=message.document.file_id,
                    thumb=getattr(message.document, "thumbs", [None])[0] if getattr(message.document, "thumbs", None) else None,
                    caption=caption,
                    caption_entities=entities,
                    force_document=True,
                    reply_to_message_id=reply_to_msg_id
                )

            # 5. PHOTO
            elif getattr(message, "photo", None):
                sent = await self.user.send_photo(
                    chat_id=target_chat,
                    photo=message.photo.file_id,
                    caption=caption,
                    caption_entities=entities,
                    reply_to_message_id=reply_to_msg_id
                )

            # 6. VIDEO
            elif getattr(message, "video", None):
                sent = await self.user.send_video(
                    chat_id=target_chat,
                    video=message.video.file_id,
                    caption=caption,
                    caption_entities=entities,
                    duration=message.video.duration,
                    width=message.video.width,
                    height=message.video.height,
                    thumb=getattr(message.video, "thumbs", [None])[0] if getattr(message.video, "thumbs", None) else None,
                    reply_to_message_id=reply_to_msg_id
                )

            # 7. AUDIO
            elif getattr(message, "audio", None):
                sent = await self.user.send_audio(
                    chat_id=target_chat,
                    audio=message.audio.file_id,
                    caption=caption,
                    caption_entities=entities,
                    duration=message.audio.duration,
                    performer=message.audio.performer,
                    title=message.audio.title,
                    reply_to_message_id=reply_to_msg_id
                )

            # 8. VOICE
            elif getattr(message, "voice", None):
                sent = await self.user.send_voice(
                    chat_id=target_chat,
                    voice=message.voice.file_id,
                    caption=caption,
                    caption_entities=entities,
                    duration=message.voice.duration,
                    reply_to_message_id=reply_to_msg_id
                )

            # 9. VIDEO NOTE
            elif getattr(message, "video_note", None):
                 sent = await self.user.send_video_note(
                    chat_id=target_chat,
                    video_note=message.video_note.file_id,
                    duration=message.video_note.duration,
                    length=message.video_note.length,
                    thumb=getattr(message.video_note, "thumbs", [None])[0] if getattr(message.video_note, "thumbs", None) else None,
                    reply_to_message_id=reply_to_msg_id
                 )

            # 10. ANIMATION (GIF)
            elif getattr(message, "animation", None):
                sent = await self.user.send_animation(
                    chat_id=target_chat,
                    animation=message.animation.file_id,
                    caption=caption,
                    caption_entities=entities,
                    width=message.animation.width,
                    height=message.animation.height,
                    duration=message.animation.duration,
                    reply_to_message_id=reply_to_msg_id
                )

            # 11. STICKER
            elif getattr(message, "sticker", None):
                sent = await self.user.send_sticker(
                    chat_id=target_chat,
                    sticker=message.sticker.file_id,
                    reply_to_message_id=reply_to_msg_id
                )

             # 12. CONTACT
            elif getattr(message, "contact", None):
                sent = await self.user.send_contact(
                    chat_id=target_chat,
                    phone_number=message.contact.phone_number,
                    first_name=message.contact.first_name,
                    last_name=message.contact.last_name,
                    vcard=message.contact.vcard,
                    reply_to_message_id=reply_to_msg_id
                )

            # 13. LOCATION
            elif getattr(message, "location", None) and not getattr(message, "venue", None):
                 sent = await self.user.send_location(
                    chat_id=target_chat,
                    latitude=message.location.latitude,
                    longitude=message.location.longitude,
                    reply_to_message_id=reply_to_msg_id
                 )
            
            # 14. VENUE
            elif getattr(message, "venue", None):
                 sent = await self.user.send_venue(
                    chat_id=target_chat,
                    latitude=message.venue.location.latitude,
                    longitude=message.venue.location.longitude,
                    title=message.venue.title,
                    address=message.venue.address,
                    foursquare_id=message.venue.foursquare_id,
                    foursquare_type=message.venue.foursquare_type,
                    reply_to_message_id=reply_to_msg_id
                 )

            # 15. FALLBACK: COPY MESSAGE
            if sent is None:
                # Fallback for complex types or unknown
                sent = await self.user.copy_message(
                    chat_id=target_chat,
                    from_chat_id=source_chat,
                    message_id=message.id,
                    caption=caption, # Try to apply rewritten caption if supported for the type
                    reply_to_message_id=reply_to_msg_id
                )
            
            # Record mapping
            if sent:
                sent_id = getattr(sent, "id", None)
                if sent_id:
                    self.store.set_mapping(source_chat, message.id, target_chat, sent_id)
                    LOGGER(__name__).info(f"Cloned {source_chat}/{message.id} -> {target_chat}/{sent_id}")
                    return sent_id
                    
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            if retry_count < 3:
                return await self._copy_message(source_chat, target_chat, message, retry_count + 1)
        except Exception as e:
            # Check for restriction errors
            err_str = str(e).upper()
            if "CHAT_FORWARDS_RESTRICTED" in err_str or "FILEREF" in err_str or "MEDIA_CAPTION_TOO_LONG" in err_str or "WEBPAGE_CURL_FAILED" in err_str:
                LOGGER(__name__).warning(f"Restriction detected ({e}), attempting bypass logic...")
                # Prepare caption with link rewriting (already done above but strictly pass it)
                bypass_caption = caption 
                
                # Check for protected content flag or simply try bypass
                bypass_res = await self._bypass_restriction(source_chat, target_chat, message, bypass_caption, reply_to_msg_id)
                if bypass_res:
                    # Store mapping since we manually sent it
                    self.store.set_mapping(source_chat, message.id, target_chat, bypass_res)
                    return bypass_res
            
            LOGGER(__name__).error(f"Error copying {source_chat}/{message.id}: {type(e).__name__}: {e}")
            return None
    
    async def handle_new_message(self, message: Message) -> None:
        """Handle a new message from a monitored channel."""
        if not self.is_enabled():
            return
        
        if getattr(message, "outgoing", False):
            return
        
        if message.edit_date:
            return
        
        source_chat = message.chat.id
        targets = self.get_targets_for_source(source_chat)
        
        if not targets:
            return
        
        for target_chat in targets:
            try:
                await self._copy_message(source_chat, target_chat, message)
                # Update sync state
                self.store.set_last_synced_id(source_chat, target_chat, message.id)
            except Exception as e:
                LOGGER(__name__).error(f"Failed to replicate to {target_chat}: {e}")
    
    async def backfill(
        self, 
        source_chat: int, 
        target_chat: int,
        start_id: Optional[int] = None,
        progress_callback: Optional[callable] = None,
        batch_size: int = 100,
    ) -> Dict:
        """
        Backfill messages from source to target, starting from last synced position.
        Returns statistics about the backfill operation.
        Uses batching for memory efficiency on large channels.
        """
        stats = {"processed": 0, "cloned": 0, "skipped": 0, "failed": 0}
        
        # Get the last synced position or start from provided ID
        if start_id is not None:
            last_synced = start_id - 1
        else:
            last_synced = self.store.get_last_synced_id(source_chat, target_chat)
        
        LOGGER(__name__).info(f"Starting backfill {source_chat} -> {target_chat} from msg #{last_synced + 1}")
        
        try:
            # Memory-efficient: process in batches instead of loading all messages
            processed_count = 0
            total_estimated = 0
            
            # First, get a rough count by fetching a small sample
            sample_msgs = []
            async for msg in self.user.get_chat_history(source_chat, limit=1):
                if msg.id > last_synced:
                    total_estimated = msg.id - last_synced
                break
            
            LOGGER(__name__).info(f"Estimated ~{total_estimated} messages to process")
            
            # Process in reverse order (oldest first) using offset
            offset = 0
            while True:
                batch = []
                async for msg in self.user.get_chat_history(source_chat, limit=batch_size, offset=offset):
                    if msg.id > last_synced:
                        batch.append(msg)
                    else:
                        break
                
                if not batch:
                    break
                
                # Sort batch by message ID (oldest first)
                batch.sort(key=lambda m: m.id)
                
                for msg in batch:
                    stats["processed"] += 1
                    processed_count += 1
                    
                    # Check if already cloned
                    if self.store.is_cloned(source_chat, msg.id, target_chat):
                        stats["skipped"] += 1
                        self.store.set_last_synced_id(source_chat, target_chat, msg.id)
                        continue
                    
                    try:
                        result = await self._copy_message(source_chat, target_chat, msg)
                        if result:
                            stats["cloned"] += 1
                        else:
                            stats["skipped"] += 1
                        
                        self.store.set_last_synced_id(source_chat, target_chat, msg.id)
                        
                    except Exception as e:
                        stats["failed"] += 1
                        LOGGER(__name__).error(f"Backfill failed for {msg.id}: {e}")
                    
                    if progress_callback:
                        try:
                            await progress_callback(processed_count, total_estimated, stats)
                        except Exception:
                            pass
                    
                    # Rate limiting - be gentle to avoid floodwait
                    await asyncio.sleep(1.5)
                
                offset += batch_size
                
                # Safety check - prevent infinite loops
                if offset > 100000:
                    LOGGER(__name__).warning("Backfill safety limit reached (100k messages)")
                    break
            
        except Exception as e:
            LOGGER(__name__).error(f"Backfill error: {e}")
        
        # Clear media group cache after backfill
        self._media_group_cache.clear()
        
        return stats
    
    async def start_continuous_backfill(
        self, 
        source_chat: int, 
        target_chat: int,
        progress_message: Optional[Message] = None,
    ) -> None:
        """Start continuous backfill that checks for new messages periodically."""
        task_key = f"{source_chat}_{target_chat}"
        
        if task_key in self.backfill_tasks:
            task = self.backfill_tasks[task_key]
            if not task.done():
                LOGGER(__name__).info(f"Backfill already running for {task_key}")
                return
        
        async def _backfill_loop():
            while self.is_enabled():
                try:
                    stats = await self.backfill(source_chat, target_chat)
                    if stats["cloned"] > 0:
                        LOGGER(__name__).info(f"Backfill cycle: cloned {stats['cloned']} messages")
                    
                    # Wait before next check (5 minutes)
                    await asyncio.sleep(300)
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    LOGGER(__name__).error(f"Backfill loop error: {e}")
                    await asyncio.sleep(60)  # Wait before retry on error
        
        task = asyncio.create_task(_backfill_loop())
        self.backfill_tasks[task_key] = task
    
    def stop_backfill(self, source_chat: int, target_chat: int) -> bool:
        """Stop a running backfill task."""
        task_key = f"{source_chat}_{target_chat}"
        if task_key in self.backfill_tasks:
            task = self.backfill_tasks[task_key]
            if not task.done():
                task.cancel()
                del self.backfill_tasks[task_key]
                return True
        return False
    
    def stop_all_backfills(self) -> int:
        """Stop all running backfill tasks."""
        count = 0
        for key, task in list(self.backfill_tasks.items()):
            if not task.done():
                task.cancel()
                count += 1
        self.backfill_tasks.clear()
        return count
    
    def get_status(self) -> Dict:
        """Get current replication status."""
        mappings = self.get_mappings()
        status = {
            "enabled": self.is_enabled(),
            "mappings": [],
            "active_backfills": len([t for t in self.backfill_tasks.values() if not t.done()]),
        }
        
        for m in mappings:
            source = m.get("source")
            target = m.get("target")
            stats = self.store.get_stats(source, target)
            status["mappings"].append({
                "source": source,
                "target": target,
                "enabled": m.get("enabled", True),
                "cloned_count": stats.get("cloned_count", 0),
                "last_synced_id": stats.get("last_synced_id", 0),
            })
        
        return status
