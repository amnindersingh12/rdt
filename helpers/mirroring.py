import os
import sqlite3
from typing import Optional, List, Tuple

from pyrogram import Client
from pyrogram.types import Message

from logger import LOGGER
from helpers.config_store import load_config


class MirrorStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        return sqlite3.connect(self.path, timeout=30)

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS mirror_map ("
                "source_chat INTEGER NOT NULL,"
                "source_msg INTEGER NOT NULL,"
                "target_chat TEXT NOT NULL,"
                "target_msg INTEGER NOT NULL,"
                "PRIMARY KEY(source_chat, source_msg, target_chat)"
                ")"
            )
            con.commit()

    def set_mapping(self, source_chat: int, source_msg: int, target_chat: str, target_msg: int) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO mirror_map(source_chat, source_msg, target_chat, target_msg) VALUES(?,?,?,?)",
                (int(source_chat), int(source_msg), str(target_chat), int(target_msg)),
            )
            con.commit()

    def get_mapping(self, source_chat: int, source_msg: int, target_chat: str) -> Optional[int]:
        with self._connect() as con:
            cur = con.execute(
                "SELECT target_msg FROM mirror_map WHERE source_chat=? AND source_msg=? AND target_chat=?",
                (int(source_chat), int(source_msg), str(target_chat)),
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                return int(row[0])
            except Exception:
                return None


class MirrorManager:
    def __init__(self, user: Client, channel_cloner) -> None:
        self.user = user
        self.channel_cloner = channel_cloner
        db_path = os.environ.get("MIRROR_DB_PATH", "mirror_map.sqlite")
        self.store = MirrorStore(db_path)

    def _targets_for_message(self, message: Message) -> List[str]:
        cfg = load_config()
        if not cfg.get("mirror_enabled"):
            return []
        rules = cfg.get("mirror_rules")
        if not isinstance(rules, dict) or not rules:
            return []

        try:
            chat_username = (message.chat.username or "").lower()
            chat_id_str = str(message.chat.id)
        except Exception:
            return []

        for src, targets in rules.items():
            s = str(src).lower()
            if s == chat_username or s == chat_id_str:
                if isinstance(targets, list):
                    return [str(t) for t in targets if str(t).strip()]
                return []
        return []

    async def handle_new_message(self, message: Message) -> None:
        if getattr(message, "outgoing", False):
            return
        if message.edit_date:
            return
        targets = self._targets_for_message(message)
        if not targets:
            return

        for target in targets:
            try:
                target_msg_id = await self.channel_cloner._copy_single_message(
                    message.chat.id,
                    target,
                    message.id,
                    None,
                    return_message_id=True,
                )
                if target_msg_id:
                    self.store.set_mapping(message.chat.id, message.id, str(target), int(target_msg_id))
            except Exception as e:
                LOGGER(__name__).error(f"Mirror copy failed: {e}")

    async def handle_edited_message(self, message: Message) -> None:
        if getattr(message, "outgoing", False):
            return
        targets = self._targets_for_message(message)
        if not targets:
            return

        for target in targets:
            try:
                target_msg_id = self.store.get_mapping(message.chat.id, message.id, str(target))
                if not target_msg_id:
                    continue

                if message.text and not message.media:
                    await self.user.edit_message_text(
                        chat_id=target,
                        message_id=target_msg_id,
                        text=message.text,
                        entities=getattr(message, "entities", None),
                    )
                elif message.caption and message.media:
                    await self.user.edit_message_caption(
                        chat_id=target,
                        message_id=target_msg_id,
                        caption=message.caption,
                        caption_entities=getattr(message, "caption_entities", None),
                    )
            except Exception as e:
                LOGGER(__name__).error(f"Mirror edit failed: {e}")
