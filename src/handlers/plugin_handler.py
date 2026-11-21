#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin watchers message handler."""

import re
from src.config.config import config
from src.services.logger import log


class PluginHandler:
    """Dispatches Telegram events to registered plugin message watchers."""

    def __init__(self, bot):
        self.bot = bot

    async def handle_watchers(self, event) -> None:
        """Processes event across registered plugin watchers."""
        if config.allowed_users and event.sender_id not in config.allowed_users:
            return

        plugin_service = getattr(self.bot, "plugin_service", None)
        if not plugin_service:
            return

        watchers = plugin_service.get_watchers()
        for info in watchers:
            plugin_inst = plugin_service.get_plugin_instance(info.plugin_name)
            if not plugin_inst:
                continue

            try:
                if info.kwargs.get("only_private") and not event.is_private:
                    continue
                if info.kwargs.get("only_groups") and event.is_private:
                    continue
                if info.kwargs.get("from_users"):
                    if event.sender_id not in info.kwargs["from_users"]:
                        continue

                pattern = info.kwargs.get("pattern")
                if pattern:
                    text = event.raw_text or ""
                    if info.kwargs.get("regex"):
                        if not re.search(pattern, text):
                            continue
                    else:
                        if pattern not in text:
                            continue

                log.debug(f"Watcher {info.handler.__name__} matched for plugin {info.plugin_name}")
                await info.handler(plugin_inst, event)
            except Exception as e:
                log.error(f"Watcher {info.handler.__name__} error ({info.plugin_name}): {e}")
