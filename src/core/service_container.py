#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dependency injection service container."""

from typing import Optional
from src.config.config import config
from src.core.client import Client
from src.services.database_service import DatabaseService
from src.services.logger import log
from src.services.media_service import MediaService
from src.services.plugin_service import PluginService
from src.services.scheduler_service import SchedulerService
from src.services.task_service import TaskService
from src.services.telegram_service import TelegramService
from src.services.translator import lang


class ServiceContainer:
    """Central registry providing access to all application singletons and services."""

    def __init__(self):
        self.config = config
        self.log = log
        self.lang = lang
        self.database = DatabaseService()
        self.scheduler = SchedulerService()

        self.client_wrapper = Client()
        self.client = self.client_wrapper.get_client()

        self.media = MediaService(self.client)
        self.telegram = TelegramService(self.client, self.media)
        self.task_service = TaskService(self.database, self.telegram, self.scheduler)
        self.plugin_service = PluginService(self.client, self.database, None)

        # Inline system — initialised asynchronously in Bootstrap
        self.inline_manager = None
