#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop all running tasks command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class StopCommand(BaseCommand):
    """Halts all active task workers."""

    async def execute(self, event) -> None:
        if self.task_service:
            await self.task_service.stop_all()
        elif hasattr(self.bot, "stop_all"):
            await self.bot.stop_all()

        await self._respond(event, lang.get("commands.stop", "Ⅱ All tasks stopped"))
