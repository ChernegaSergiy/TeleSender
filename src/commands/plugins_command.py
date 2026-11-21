#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List loaded plugins command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class PluginsCommand(BaseCommand):
    """Displays loaded plugins and their versions."""

    async def execute(self, event) -> None:
        plugin_service = getattr(self.bot, "plugin_service", None)
        if not plugin_service:
            await self._respond(event, lang.get("messages.no_plugins", "No plugins available"))
            return

        plugins = plugin_service.get_loaded_plugins()
        if not plugins:
            await self._respond(event, lang.get("messages.no_plugins", "No plugins loaded"))
            return

        header = lang.get("messages.plugins_loaded_list", "**Loaded plugins:**")
        msg = f"{header}\n\n"
        for name in plugins:
            meta = plugin_service.get_plugin_metadata(name)
            if meta:
                msg += f"• **{name}** v{meta.version} - {meta.description}\n"
            else:
                msg += f"• **{name}**\n"

        await self._respond(event, msg)
