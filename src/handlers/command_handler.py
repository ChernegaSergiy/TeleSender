#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command routing handler."""

from typing import Dict
from src.commands import (
    ApiIdCommand,
    BaseCommand,
    AddTaskCommand,
    EditTaskCommand,
    HelpCommand,
    IdCommand,
    PluginsCommand,
    StartTaskCommand,
    StatsCommand,
    StopCommand,
    StopTaskCommand,
    TasksCommand,
    PluginCloneCommand,
    PluginUpdateCommand,
)
from src.config.config import config
from src.services.database_service import Colors, print_color
from src.services.logger import log
from src.services.translator import lang


class CommandHandler:
    """Dispatches user Telegram commands to command classes or plugins."""

    def __init__(self, bot):
        self.bot = bot
        self.commands: Dict[str, BaseCommand] = {
            "id": IdCommand(bot),
            "apibid": ApiIdCommand(bot),
            "tasks": TasksCommand(bot),
            "stats": StatsCommand(bot),
            "stop": StopCommand(bot),
            "start_task": StartTaskCommand(bot),
            "stop_task": StopTaskCommand(bot),
            "plugins": PluginsCommand(bot),
            "clone": PluginCloneCommand(bot),
            "pclone": PluginCloneCommand(bot),
            "install": PluginCloneCommand(bot),
            "pinstall": PluginCloneCommand(bot),
            "update_plugin": PluginUpdateCommand(bot),
            "pupdate": PluginUpdateCommand(bot),
            "update": PluginUpdateCommand(bot),
            "add": AddTaskCommand(bot),
    "edit": EditTaskCommand(bot),
    "help": HelpCommand(bot),
        }

    async def _respond(self, event, text: str):
        """Edit the message if outgoing, otherwise reply."""
        if getattr(event, "out", False):
            await event.edit(text)
        else:
            await event.reply(text)

    async def resolve_sender_id(self, event):
        """Resolves sender ID reliably across incoming and self/outgoing messages."""
        sender_id = event.sender_id
        if sender_id is not None:
            return sender_id

        if getattr(event, "out", False):
            try:
                me = await event.client.get_me()
                if me:
                    return me.id
            except Exception:
                pass

        if hasattr(event, "chat_id") and getattr(event, "is_private", False):
            return event.chat_id

        return None

    async def handle(self, event) -> bool:
        """Handles incoming command event. Returns True if handled."""
        text = event.raw_text
        if not text or not text.startswith("."):
            return False

        sender_id = await self.resolve_sender_id(event)

        # Check permission silently
        if config.allowed_users:
            if sender_id not in config.allowed_users:
                is_owner = False
                try:
                    me = await event.client.get_me()
                    if me and sender_id == me.id:
                        is_owner = True
                except Exception:
                    pass

                if not is_owner:
                    return False

        cmd_part = text.split()[0][1:]
        cmd_name = cmd_part.split("@")[0].lower()
        log.info(f"Command .{cmd_name} from {sender_id}")

        # Check plugin commands first (if registered)
        plugin_service = getattr(self.bot, "plugin_service", None)
        if plugin_service and cmd_name and not cmd_name.startswith("_"):
            cmd_info = plugin_service.get_command(cmd_name)
            if cmd_info:
                plugin_inst = plugin_service.get_plugin_instance(cmd_info.plugin_name)
                if plugin_inst:
                    try:
                        await cmd_info.handler(plugin_inst, event)
                        log.info(f"Plugin command .{cmd_name} handled by {cmd_info.plugin_name}")
                        return True
                    except Exception as e:
                        log.exception(f"Error in plugin command .{cmd_name}: {e}")
                        err_msg = lang.get("messages.debug_command_error", "Command error: {error}").format(error=str(e))
                        await self._respond(event, err_msg)
                        return True

        # Check built-in commands
        if cmd_name in self.commands:
            try:
                await self.commands[cmd_name].execute(event)
                log.debug(f"Built-in command .{cmd_name} handled")
                return True
            except Exception as e:
                log.exception(f"Error executing command .{cmd_name}: {e}")
                err_msg = lang.get("messages.debug_command_error", "Command error: {error}").format(error=str(e))
                await self._respond(event, err_msg)
                return True

        log.debug(f"Unhandled command .{cmd_name} from {sender_id}")
        return False
