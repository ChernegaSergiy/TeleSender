#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API ID command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class ApiIdCommand(BaseCommand):
    """Replies with resolved Telegram API ID of the chat."""

    async def execute(self, event) -> None:
        chat_id = event.chat_id
        api_id = str(chat_id)[4:] if str(chat_id).startswith("-100") else str(chat_id)
        reply = lang.get("commands.apibid", "**API ID:** `{api_id}`").format(api_id=api_id)
        await self._respond(event, reply)
