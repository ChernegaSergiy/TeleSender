#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Help command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class HelpCommand(BaseCommand):
    """Replies with available bot commands guide."""

    async def execute(self, event) -> None:
        help_text = lang.get(
            "commands.help",
            "**AVAILABLE COMMANDS:**\n\n"
            ".id - Chat ID\n"
            ".id @username - User ID\n"
            ".id (as a reply) - User info\n"
            ".apibid - API ID\n"
            ".tasks - Task list\n"
            ".stats - Statistics\n"
            ".start_task <ID> - Start task\n"
            ".stop_task <ID> - Stop task\n"
            ".stop - Stop all tasks\n"
            ".plugins - Loaded plugins\n"
            ".clone <url> - Clone plugin\n"
            ".update [name] - Update plugin(s)\n"
            ".add - Add task\n"
            ".edit <ID> - Edit task\n"
            ".help - Help manual",
        )
        await self._respond(event, help_text)
