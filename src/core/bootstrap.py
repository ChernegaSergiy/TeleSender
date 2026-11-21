#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Application bootstrap and initialization routines."""

import asyncio
from datetime import datetime
from telethon import events

from src.config.config import config
from src.core.service_container import ServiceContainer
from src.handlers.command_handler import CommandHandler
from src.handlers.plugin_handler import PluginHandler
from src.inline.manager import InlineManager
from src.services.database_service import Colors, print_color
from src.services.logger import log
from src.services.translator import lang


class Bootstrap:
    """Bootstraps application components, authorization, plugins and event loops."""

    def __init__(self, container: ServiceContainer, bot):
        self.container = container
        self.bot = bot
        self.client = container.client
        self.command_handler = CommandHandler(bot)
        self.plugin_handler = PluginHandler(bot)

    async def initialize(self):
        """Starts client session, registers handlers, and loads initial state."""
        me = await self.container.client_wrapper.start()
        self.bot._last_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"Telegram client started: id={me.id} username={me.username or '-'}")

        # Update plugin loader references
        self.container.plugin_service.set_dependencies(
            client=self.client,
            db=self.container.database,
            bot=self.bot,
        )

        # Initialise the inline system (bot creation + polling)
        print_color(lang.get("messages.inline_initializing", "Initializing inline bot system..."), Colors.CYAN)
        inline_mgr = InlineManager(
            client=self.client,
            db=self.container.database,
            owner_id=config.my_user_id,
            plugin_registry=self.container.plugin_service.registry,
            allowed_users=config.allowed_users,
        )
        inline_ok = await inline_mgr.init()
        if inline_ok:
            self.container.inline_manager = inline_mgr
            self.bot.inline = inline_mgr
            print_color(
                lang.get("messages.inline_ready", "Inline bot ready: @{username}").format(username=inline_mgr.bot_username), Colors.GREEN
            )
        else:
            print_color(lang.get("messages.inline_not_available", "Inline system not available"), Colors.YELLOW)

        # Inject inline manager into plugin loader so new plugins get it
        self.container.plugin_service.loader.set_inline(inline_mgr if inline_ok else None)

        # Load plugins
        plugins_loading_msg = lang.get("messages.plugins_loading", "Loading plugins...")
        print_color(plugins_loading_msg, Colors.CYAN)
        loaded = await self.container.plugin_service.load_all()
        if loaded > 0:
            loaded_msg = lang.get("messages.plugins_loaded", "Loaded {loaded} plugins").replace("{loaded}", str(loaded))
            print_color(f"{loaded_msg}", Colors.GREEN)
        else:
            no_plugins_msg = lang.get("messages.no_plugins", "No plugins found")
            print_color(no_plugins_msg, Colors.YELLOW)

        # Check allowed users
        if not config.allowed_users:
            your_id_msg = lang.get("auth.your_id", "YOUR TELEGRAM ID")
            add_cfg_msg = lang.get("auth.add_to_config_allowed", "Add to config.py: ALLOWED_USERS = [{id}]").replace("{id}", str(me.id))
            print_color(f"\n{your_id_msg}: {me.id}", Colors.YELLOW)
            print_color(f"{add_cfg_msg}\n", Colors.YELLOW)
        else:
            if me.id in config.allowed_users:
                auth_msg = lang.get("auth.authorized", "Authorized: {name}").replace("{name}", me.first_name or "")
                print_color(f"{auth_msg}", Colors.GREEN)
            else:
                your_id_msg = lang.get("auth.your_id", "YOUR TELEGRAM ID")
                not_allowed_msg = lang.get("auth.not_in_allowed", "is not in allowed users list")
                print_color(f"{your_id_msg}: {me.id} {not_allowed_msg}", Colors.YELLOW)

        # Register message events
        @self.client.on(events.NewMessage)
        async def on_new_message(event):
            handled = await self.command_handler.handle(event)
            if not handled:
                await self.plugin_handler.handle_watchers(event)

        # Start active background tasks
        active_tasks = self.container.database.get_active()
        for task in active_tasks:
            await self.container.task_service.start_task(task[0])

        log.info(f"Bot startup complete: {loaded} plugin(s), {len(active_tasks)} active task(s)")
