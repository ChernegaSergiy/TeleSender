#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fallback message handler."""

from src.config.config import config
from src.services.gallery_service import gallery_service
from src.services.logger import log
from src.services.translator import lang


class MessageHandler:
    """Fallback handler for unhandled incoming messages."""

    def __init__(self, bot):
        self.bot = bot

    async def handle(self, event) -> None:
        """Handles unrouted messages."""
        if config.allowed_users and event.sender_id not in config.allowed_users:
            return
        if getattr(event, "media", None):
            try:
                path = await event.message.download_media(
                    file=str(gallery_service.ROOT / "other") + "/"
                )
                if path:
                    gallery_service.save_file(path, "other")
                    await event.reply(lang.get("messages.saved_to_gallery", "Saved to gallery"))
            except Exception as e:
                log.error(f"Gallery download failed: {e}")
