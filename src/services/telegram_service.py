#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram messaging service handling text and media transmissions."""

import os
from pathlib import Path
from typing import Any, Optional, Union
from src.services.logger import log
from src.services.media_service import MediaService
from src.services.path_security import is_private_media_path


class TelegramService:
    """Service wrapping message sending logic across channels and chats."""

    def __init__(self, client=None, media_service: Optional[MediaService] = None):
        self.client = client
        self.media_service = media_service or MediaService(client)

    def set_client(self, client):
        self.client = client
        if self.media_service:
            self.media_service.set_client(client)

    @staticmethod
    def detect_parse_mode(text: Optional[str]) -> Optional[str]:
        """Auto-detects whether text contains HTML or Markdown tags."""
        if not text:
            return None

        html_patterns = [
            "<b>", "<i>", "<u>", "<s>", "<a ", "<code>", "<pre>",
            "<strong>", "<em>", "<tg-emoji", "<tgemoji", "<blockquote", "<span class=\"tg-spoiler\">",
            "<spoiler>", "</b>", "</i>", "</u>", "</s>", "</a>", "</code>", "</pre>", "</tg-emoji>"
        ]
        for p in html_patterns:
            if p in text:
                return "html"

        md_patterns = ["**", "__", "*", "_", "~~", "[", "`", "```"]
        for p in md_patterns:
            if p in text:
                return "md"

        return None

    def _resolve_target(self, chat_id: Union[str, int], reply_to: Optional[int] = None):
        """Resolves raw chat string into target entity/ID and reply_to."""
        target_chat = chat_id
        resolved_reply = reply_to

        if isinstance(chat_id, str):
            if chat_id.startswith("https://t.me/c/"):
                parts = chat_id.rstrip("/").split("/")
                if len(parts) >= 5 and parts[-2].isdigit():
                    group_id = int(parts[-2])
                    target_chat = int(f"-100{group_id}")
                    if len(parts) >= 6 and parts[-1].isdigit():
                        resolved_reply = int(parts[-1])
            elif "_" in chat_id:
                parts = chat_id.split("_")
                if len(parts) == 2 and parts[0].lstrip("-").isdigit() and parts[1].isdigit():
                    target_chat = int(parts[0])
                    resolved_reply = int(parts[1])
            elif chat_id.lstrip("-").isdigit():
                target_chat = int(chat_id)

        return target_chat, resolved_reply

    async def send_text(self, chat_id: Union[str, int], msg: str, reply_to: Optional[int] = None) -> bool:
        """Sends a text message to chat."""
        if not self.client:
            log.error("Telegram client not available for send_text")
            return False

        try:
            target_chat, target_reply = self._resolve_target(chat_id, reply_to)
            parse_mode = self.detect_parse_mode(msg)

            if parse_mode:
                await self.client.send_message(target_chat, msg, reply_to=target_reply, parse_mode=parse_mode)
            else:
                await self.client.send_message(target_chat, msg, reply_to=target_reply)
            log.debug(f"Sent text message to {target_chat}")
            return True
        except Exception as e:
            log.error(f"Failed to send text to {chat_id}: {e}")
            return False

    async def send_media(
        self,
        chat_id: Union[str, int],
        media_path: str,
        media_type: str,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
    ) -> bool:
        """Sends media file with optional caption."""
        if not self.client:
            log.error("Telegram client not available for send_media")
            return False

        try:
            cached = await self.media_service.get_cached_media(media_path, media_type)
            if not cached:
                return False

            target_chat, target_reply = self._resolve_target(chat_id, reply_to)
            parse_mode = self.detect_parse_mode(caption) if caption else None

            if media_type == "photo":
                await self.client.send_file(target_chat, cached, caption=caption, reply_to=target_reply, parse_mode=parse_mode)
            elif media_type == "video":
                await self.client.send_file(target_chat, cached, caption=caption, reply_to=target_reply, parse_mode=parse_mode)
            elif media_type == "document":
                await self.client.send_file(
                    target_chat, cached, caption=caption, reply_to=target_reply, force_document=True, parse_mode=parse_mode
                )
            elif media_type == "audio":
                await self.client.send_file(target_chat, cached, caption=caption, reply_to=target_reply, parse_mode=parse_mode)
            elif media_type == "sticker":
                await self.client.send_file(target_chat, cached, reply_to=target_reply)
            elif media_type == "gif":
                await self.client.send_file(
                    target_chat, cached, caption=caption, reply_to=target_reply, parse_mode=parse_mode, file_type="animation"
                )
            elif media_type == "voice":
                await self.client.send_file(
                    target_chat, cached, caption=caption, reply_to=target_reply, parse_mode=parse_mode, voice_note=True
                )
            elif media_type == "video_note":
                await self.client.send_file(target_chat, cached, reply_to=target_reply, video_note=True)
            else:
                await self.client.send_file(target_chat, cached, caption=caption, reply_to=target_reply, parse_mode=parse_mode)
            log.debug(f"Sent {media_type} to {target_chat}")
            return True
        except Exception as e:
            log.error(f"Failed to send media to {chat_id}: {e}")
            return False

    async def send(
        self,
        chat_id: Union[str, int],
        msg: str,
        media_path: Optional[str] = None,
        media_type: Optional[str] = None,
        reply_to: Optional[int] = None,
    ) -> bool:
        """Dispatches message as either media or pure text."""
        if media_path:
            try:
                resolved = Path(media_path).expanduser().resolve(strict=False)
            except Exception:
                log.warning("Rejected media path: invalid path")
                return False
            if is_private_media_path(resolved):
                log.warning("Blocked attempt to send private media path")
                return False
            if resolved.exists() and resolved.is_file():
                return await self.send_media(chat_id, str(resolved), media_type or "photo", msg, reply_to)
        return await self.send_text(chat_id, msg, reply_to)
