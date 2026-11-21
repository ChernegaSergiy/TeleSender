#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add task via Telegram dialog."""

from src.commands.base_command import BaseCommand
from src.services.logger import log
from src.services.telegram_dialog import TelegramDialog
from src.services.translator import lang


class AddTaskCommand(BaseCommand):
    async def execute(self, event):
        dialog = TelegramDialog(self.bot, event, mode="add")
        await dialog.start()
        log.info("AddTaskCommand invoked (dialog)")