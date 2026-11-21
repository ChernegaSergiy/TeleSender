#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Service managing media caching and uploading to Telegram."""

import os
from typing import Any, Dict, Optional
from src.services.logger import log
from src.services.translator import lang


class MediaService:
    """Caches uploaded media references to avoid redundant Telegram uploads."""

    def __init__(self, client=None):
        self.client = client
        self._media_cache: Dict[str, Any] = {}

    def set_client(self, client):
        self.client = client

    async def get_cached_media(self, media_path: str, media_type: str) -> Optional[Any]:
        """Returns cached Telegram input media or uploads it."""
        if media_path in self._media_cache:
            return self._media_cache[media_path]

        if not os.path.exists(media_path):
            not_found_msg = lang.get("messages.file_not_found", "File not found: {path}").replace("{path}", media_path)
            log.error(not_found_msg)
            return None

        if not self.client:
            log.error("Telegram client is not initialized in MediaService")
            return None

        try:
            result = await self.client.upload_file(media_path)
            self._media_cache[media_path] = result
            return result
        except Exception as e:
            log.error(f"Media upload error ({media_path}): {e}")
            return None

    def clear_media_cache(self) -> int:
        """Clears media cache and returns number of cleared items."""
        count = len(self._media_cache)
        self._media_cache.clear()
        return count
