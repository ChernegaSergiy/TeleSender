#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram client wrapper."""

import os
from typing import Optional
from telethon import TelegramClient

from src.config.config import config
from src.services.logger import log


class Client:
    """Wrapper managing TelegramClient lifecycle and session location."""

    def __init__(self, session_name: Optional[str] = None):
        self.session_path = session_name or config.session_path
        self.telethon_client = TelegramClient(self.session_path, config.api_id, config.api_hash)

    def get_client(self) -> TelegramClient:
        return self.telethon_client

    async def start(self, phone: Optional[str] = None):
        phone_num = phone or config.phone_number
        await self.telethon_client.start(phone=phone_num)
        return await self.telethon_client.get_me()

    async def disconnect(self):
        try:
            await self.telethon_client.disconnect()
        except Exception as e:
            log.error(f"Disconnect error: {e}")
