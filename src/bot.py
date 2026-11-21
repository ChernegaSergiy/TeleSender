#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main Bot class coordinating application services and background loops."""

import asyncio
from typing import Any, Dict, List, Optional

from src.core.bootstrap import Bootstrap
from src.core.service_container import ServiceContainer
from src.services.database_service import DatabaseService
from src.services.logger import log
from src.services.media_service import MediaService
from src.services.plugin_service import PluginService
from src.services.scheduler_service import SchedulerService
from src.services.task_service import TaskService
from src.services.telegram_service import TelegramService
from src.services.translator import lang
from src.web.server import WebServerManager


class Bot:
    """Core bot controller delegating specialized concerns to services."""

    def __init__(self, container: Optional[ServiceContainer] = None):
        self.container = container or ServiceContainer()

        self.client = self.container.client
        self.db: DatabaseService = self.container.database
        self.scheduler_service: SchedulerService = self.container.scheduler
        self.media_service: MediaService = self.container.media
        self.telegram_service: TelegramService = self.container.telegram
        self.task_service: TaskService = self.container.task_service
        self.plugin_service: PluginService = self.container.plugin_service

        self.plugin_loader = self.plugin_service.loader
        self.plugin_registry = self.plugin_service.registry
        self.plugin_pages = self.plugin_service.pages
        self.core_loc = self.plugin_service.localization

        self.bootstrap = Bootstrap(self.container, self)
        self.web_server = WebServerManager(self)
        self._last_start = None
        self.inline = None  # InlineManager — set by Bootstrap after init

    @property
    def running(self) -> Dict[int, bool]:
        """Dictionary of currently running task worker IDs."""
        return self.task_service.running

    async def start(self):
        """Initializes client connection, listeners, and starts tasks."""
        await self.bootstrap.initialize()

    async def start_task(self, tid: int) -> bool:
        """Starts a background worker for task ID."""
        return await self.task_service.start_task(tid)

    async def stop_task(self, tid: int):
        """Stops background worker for task ID."""
        await self.task_service.stop_task(tid)

    async def stop_all(self):
        """Stops all running background workers and inline bot."""
        await self.task_service.stop_all()
        if self.inline:
            await self.inline.stop()

    def format_interval(self, cfg: Optional[Dict[str, Any]], is_once: int = 0) -> str:
        """Formats interval configuration for user display."""
        return self.scheduler_service.format_interval(cfg, is_once)

    def parse_interval(self, interval_str: str) -> Optional[Dict[str, Any]]:
        return self.scheduler_service.parse_interval(interval_str)

    def parse_once(self, once_str: str):
        return self.scheduler_service.parse_once(once_str)

    def pluralize(self, number: int, one: str, few: str, many: str) -> str:
        return self.scheduler_service.pluralize(number, one, few, many)

    async def send(self, chat_id, msg, media_path=None, media_type=None, reply_to=None) -> bool:
        return await self.telegram_service.send(chat_id, msg, media_path, media_type, reply_to)

    async def send_text(self, chat_id, msg, reply_to=None) -> bool:
        return await self.telegram_service.send_text(chat_id, msg, reply_to)

    async def send_media(self, chat_id, media_path, media_type, caption=None, reply_to=None) -> bool:
        return await self.telegram_service.send_media(chat_id, media_path, media_type, caption, reply_to)

    def clear_media_cache(self) -> int:
        return self.media_service.clear_media_cache()
