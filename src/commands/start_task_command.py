#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Start specific task command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class StartTaskCommand(BaseCommand):
    """Starts a specific background task by ID."""

    async def execute(self, event) -> None:
        parts = event.raw_text.split()
        if len(parts) != 2:
            await self._respond(event, ".start_task <ID>")
            return
        try:
            tid = int(parts[1])
            if self.task_service:
                await self.task_service.start_task(tid)
            elif hasattr(self.bot, "start_task"):
                await self.bot.start_task(tid)

            await self._respond(event,
                lang.get("commands.start_task", "▶ Task #{tid} started").format(tid=tid)
            )
        except Exception:
            await self._respond(event, lang.get("messages.wrong_format", "Invalid format"))
