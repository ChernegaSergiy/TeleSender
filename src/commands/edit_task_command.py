#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Edit task via Telegram dialog."""

from src.commands.base_command import BaseCommand
from src.services.logger import log
from src.services.telegram_dialog import TelegramDialog
from src.services.translator import lang


class EditTaskCommand(BaseCommand):
    async def execute(self, event) -> None:
        parts = event.raw_text.split()
        task_id = None
        if len(parts) >= 2:
            try:
                task_id = int(parts[1])
            except ValueError:
                await self._respond(event, lang.get("messages.wrong_format", "Invalid format"))
                return

        if task_id is None:
            dialog = TelegramDialog(self.bot, event, mode="edit", task_id=None)
            await dialog.start()
            return

        if not self.db.get_full(task_id):
            await self._respond(event,
                lang.get("messages.task_not_found", "Task #{tid} not found").format(tid=task_id)
            )
            return

        dialog = TelegramDialog(self.bot, event, mode="edit", task_id=task_id)
        await dialog.start()
        log.info(f"EditTaskCommand invoked (dialog) for task #{task_id}")