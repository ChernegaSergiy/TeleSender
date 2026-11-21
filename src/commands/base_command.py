#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Base class for Telegram bot commands."""

from src.interfaces.command_interface import CommandInterface


class BaseCommand(CommandInterface):
    """Base command providing common access to bot services."""

    def __init__(self, bot):
        self.bot = bot
        self.client = bot.client
        self.db = bot.db
        self.task_service = getattr(bot, "task_service", None)
        self.scheduler = getattr(bot, "scheduler_service", None)

    async def _respond(self, event, text: str):
        """Edit the message if outgoing (event.out), otherwise reply."""
        if getattr(event, "out", False):
            await event.edit(text)
        else:
            await event.reply(text)
