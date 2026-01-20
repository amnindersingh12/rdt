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
            return chat.id
        except Exception as e:
            LOGGER(__name__).error(f"Failed to resolve chat ID for {identifier}: {e}")
            return None
    
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
            # Skip service messages (join, leave, pin, etc.)
            if message.service:
                LOGGER(__name__).debug(f"Skipping service message {source_chat}/{message.id}")
                return None
            
            # Check if already cloned
            if self.store.is_cloned(source_chat, message.id, target_chat):
                LOGGER(__name__).debug(f"Message {source_chat}/{message.id} already cloned to {target_chat}")
                return self.store.get_target_msg_id(source_chat, message.id, target_chat)
            
            # Determine reply_to_message_id for the target
            reply_to_msg_id = None
            if message.reply_to_message_id:
                # Look up the target message ID for the replied-to message
                reply_to_msg_id = self.store.get_target_msg_id(
                    source_chat, 
                    message.reply_to_message_id, 
                    target_chat
                )
            
            sent = None
            
            # Handle different message types
            
            # 1. POLL
            if getattr(message, "poll", None):
                poll = message.poll
                question = getattr(poll, "question", None)
                if question:
                    poll_option_texts = []
                    for option in getattr(poll, "options", []):
                        if isinstance(option, str):
                            poll_option_texts.append(option)
                        elif hasattr(option, "text"):
                            poll_option_texts.append(str(option.text))
                        else:
                            poll_option_texts.append(str(option))
                    
                    if len(poll_option_texts) >= 2:
                        poll_kwargs = {
                            "chat_id": target_chat,
                            "question": question,
                            "options": poll_option_texts,
                            "is_anonymous": getattr(poll, "is_anonymous", True),
                            "allows_multiple_answers": getattr(poll, "allows_multiple_answers", False),
                            "type": getattr(poll, "type", "regular"),
                            "correct_option_id": getattr(poll, "correct_option_id", None),
                            "explanation": getattr(poll, "explanation", None),
                            "reply_to_message_id": reply_to_msg_id,
                        }
                        try:
                            sent = await self.user.send_poll(**poll_kwargs)
                        except AttributeError:
                            poll_kwargs["options"] = [SimpleNamespace(text=t, entities=[]) for t in poll_option_texts]
                            sent = await self.user.send_poll(**poll_kwargs)
            
            # 2. CONTACT
            elif getattr(message, "contact", None):
                sent = await self.user.send_contact(
                    chat_id=target_chat,
                    phone_number=message.contact.phone_number,
                    first_name=message.contact.first_name,
                    last_name=getattr(message.contact, "last_name", "") or "",
                    vcard=getattr(message.contact, "vcard", None),
                    reply_to_message_id=reply_to_msg_id,
                )
            
            # 3. LOCATION
            elif getattr(message, "location", None) and not getattr(message, "venue", None):
                sent = await self.user.send_location(
                    chat_id=target_chat,
                    latitude=message.location.latitude,
                    longitude=message.location.longitude,
                    reply_to_message_id=reply_to_msg_id,
                )
            
            # 4. VENUE
            elif getattr(message, "venue", None):
                sent = await self.user.send_venue(
                    chat_id=target_chat,
                    latitude=message.venue.location.latitude,
                    longitude=message.venue.location.longitude,
                    title=message.venue.title,
                    address=message.venue.address,
                    foursquare_id=getattr(message.venue, "foursquare_id", None),
                    foursquare_type=getattr(message.venue, "foursquare_type", None),
                    reply_to_message_id=reply_to_msg_id,
                )
            
            # 5. DICE
            elif getattr(message, "dice", None):
                sent = await self.user.send_dice(
                    chat_id=target_chat,
                    emoji=message.dice.emoji,
                    reply_to_message_id=reply_to_msg_id,
                )
            
            # 6. MEDIA GROUP - with deduplication
            elif message.media_group_id:
                mg_key = f"{source_chat}_{message.media_group_id}_{target_chat}"
                
                # Check if we've already processed this media group
                if mg_key in self._media_group_cache:
                    LOGGER(__name__).debug(f"Media group already processed: {mg_key}")
                    return None
                
                try:
                    msgs = await self.user.get_media_group(source_chat, message.id)
                    if msgs:
                        # Only process if this is the first message in the group
                        first_msg = min(msgs, key=lambda m: m.id)
                        if message.id == first_msg.id:
                            self._media_group_cache[mg_key] = True
                            
                            sent_msgs = await self.user.copy_media_group(
                                chat_id=target_chat,
                                from_chat_id=source_chat,
                                message_id=message.id,
                                reply_to_message_id=reply_to_msg_id,
                            )
                            # Map all messages in the group
                            if sent_msgs:
                                for src_m, tgt_m in zip(msgs, sent_msgs):
                                    self.store.set_mapping(source_chat, src_m.id, target_chat, tgt_m.id)
                                LOGGER(__name__).info(f"Cloned media group ({len(sent_msgs)} items) to {target_chat}")
                                return sent_msgs[0].id
                        else:
                            # This is not the first message in the group, skip it
                            return None
                except Exception as mg_err:
                    LOGGER(__name__).warning(f"Media group copy failed, trying single: {mg_err}")
                    # Fall through to try copy_message
            
            # 7. STICKER
            elif getattr(message, "sticker", None):
                try:
                    sent = await self.user.send_sticker(
                        chat_id=target_chat,
                        sticker=message.sticker.file_id,
                        reply_to_message_id=reply_to_msg_id,
                    )
                except Exception:
                    pass  # Fall through to copy_message
            
            # 8. DOCUMENT / PDF / FILE
            elif getattr(message, "document", None):
                try:
                    sent = await self.user.send_document(
                        chat_id=target_chat,
                        document=message.document.file_id,
                        thumb=getattr(message.document, "thumbs", [None])[0] if getattr(message.document, "thumbs", None) else None,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                        file_name=message.document.file_name,
                        reply_to_message_id=reply_to_msg_id,
                    )
                except Exception as e:
                    LOGGER(__name__).warning(f"Failed to send document: {e}. Trying copy.")
                    pass # Fallback

            # 9. PHOTO
            elif getattr(message, "photo", None):
                try:
                    sent = await self.user.send_photo(
                        chat_id=target_chat,
                        photo=message.photo.file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                        reply_to_message_id=reply_to_msg_id,
                    )
                except Exception as e:
                    LOGGER(__name__).warning(f"Failed to send photo: {e}. Trying copy.")
                    pass

            # 10. VIDEO
            elif getattr(message, "video", None):
                try:
                    sent = await self.user.send_video(
                        chat_id=target_chat,
                        video=message.video.file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                        duration=message.video.duration,
                        width=message.video.width,
                        height=message.video.height,
                        thumb=getattr(message.video, "thumbs", [None])[0] if getattr(message.video, "thumbs", None) else None,
                        reply_to_message_id=reply_to_msg_id,
                    )
                except Exception as e:
                     LOGGER(__name__).warning(f"Failed to send video: {e}. Trying copy.")
                     pass

            # 11. AUDIO
            elif getattr(message, "audio", None):
                try:
                    sent = await self.user.send_audio(
                        chat_id=target_chat,
                        audio=message.audio.file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                        duration=message.audio.duration,
                        performer=message.audio.performer,
                        title=message.audio.title,
                        reply_to_message_id=reply_to_msg_id,
                    )
                except Exception as e:
                    LOGGER(__name__).warning(f"Failed to send audio: {e}. Trying copy.")
                    pass

            # 12. FORWARD (keep as forward) - only for forwarded messages without content
            elif message.forward_date and not message.media and not message.text:
                try:
                    sent_msgs = await self.user.forward_messages(
                        chat_id=target_chat,
                        from_chat_id=source_chat,
                        message_ids=message.id,
                    )
                    if sent_msgs:
                        sent = sent_msgs[0] if isinstance(sent_msgs, list) else sent_msgs
                except Exception:
                    pass  # Fall through to copy
            
            # 13. TEXT / LINK / ENTITIES
            # Explicitly fall through to copy_message as it handles text formatting and links best.
            # But we can log if it's purely text.
            elif message.text or message.caption:
                 # Just fall through
                 pass
            
            # 14. USE COPY_MESSAGE FOR EVERYTHING ELSE (preserves text, media, captions, etc.)
            if sent is None:
                try:
                    sent = await self.user.copy_message(
                        chat_id=target_chat,
                        from_chat_id=source_chat,
                        message_id=message.id,
                        reply_to_message_id=reply_to_msg_id,
                    )
                except BadRequest as e:
                    if "MESSAGE_EMPTY" in str(e) or "MEDIA_EMPTY" in str(e):
                        # Skip empty messages
                        LOGGER(__name__).debug(f"Skipping empty message {source_chat}/{message.id}")
                        return None
                    elif "CHAT_FORWARDS_RESTRICTED" in str(e):
                        # Protected channel - need to download and re-upload
                        LOGGER(__name__).debug(f"Protected content, skipping {source_chat}/{message.id}")
                        return None
                    raise
            
            # Store the mapping
            if sent:
                target_msg_id = getattr(sent, "id", None)
                if target_msg_id:
                    self.store.set_mapping(source_chat, message.id, target_chat, target_msg_id)
                    LOGGER(__name__).info(f"Cloned {source_chat}/{message.id} -> {target_chat}/{target_msg_id}")
                    return target_msg_id
            
            return None
            
        except FloodWait as e:
            wait_time = int(getattr(e, "value", 5))
            LOGGER(__name__).warning(f"FloodWait: waiting {wait_time}s...")
            await asyncio.sleep(wait_time + 1)
            if retry_count < 3:
                return await self._copy_message(source_chat, target_chat, message, retry_count + 1)
            return None
        except Exception as e:
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
