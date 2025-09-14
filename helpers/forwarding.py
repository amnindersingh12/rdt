from typing import List, Union

from pyrogram import Client
from pyrogram.types import Message

from logger import LOGGER
from helpers.config_store import (
    load_config,
    save_config,
    add_source_channel,
    remove_source_channel,
    clear_sources,
    set_target_channel,
    set_forward_enabled,
)


def normalize_identifier(s: str) -> str:
    s = s.strip()
    if s.startswith("@"):  # strip @
        return s[1:]
    if s.startswith("https://t.me/"):
        return s.rstrip("/").rsplit("/", 1)[-1]
    return s


class ForwardingManager:
    def __init__(self, user: Client) -> None:
        self.user = user

    def get_config(self):
        return load_config()

    def set_target(self, target: str):
        target = normalize_identifier(target)
        return set_target_channel(target)

    def add_sources(self, sources: List[str]):
        for s in sources:
            add_source_channel(normalize_identifier(s))
        return load_config()

    def remove_sources(self, sources: List[str]):
        for s in sources:
            remove_source_channel(normalize_identifier(s))
        return load_config()

    def clear_sources(self):
        return clear_sources()

    def enable(self, enabled: bool):
        return set_forward_enabled(enabled)

    async def handle_new_message(self, message: Message):
        cfg = load_config()
        if not cfg.get("forward_enabled"):
            return

        dest = cfg.get("destination_channel")
        if not dest:
            return

        sources: List[str] = cfg.get("source_channels", [])
        if not sources:
            return

        try:
            chat_username = (message.chat.username or "").lower()
            chat_id_str = str(message.chat.id)
        except Exception:
            return

        match = False
        for src in sources:
            s = str(src).lower()
            if s == chat_username or s == chat_id_str:
                match = True
                break

        if not match:
            return

        try:
            await self.user.forward_messages(
                chat_id=dest,
                from_chat_id=message.chat.id,
                message_ids=message.id,
            )
            LOGGER(__name__).info(
                f"Forwarded {message.id} from {message.chat.id} -> {dest}"
            )
        except Exception as e:
            LOGGER(__name__).error(f"Forward failed: {e}")
