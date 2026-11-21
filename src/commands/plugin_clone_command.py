#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clone and install a plugin from a Git repository or local path."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class PluginCloneCommand(BaseCommand):
    """Clones and installs a plugin from a Git repository URL or local directory.

    Aliases: .clone, .pclone, .install, .pinstall
    Usage:   .clone <git_url_or_path>

    If the plugin already exists it will be removed and reinstalled (overwrite).
    """

    async def execute(self, event) -> None:
        plugin_service = getattr(self.bot, "plugin_service", None)
        if not plugin_service:
            await self._respond(event, lang.get("messages.no_plugins", "Plugin service unavailable"))
            return

        text = event.raw_text or ""
        parts = text.split(maxsplit=1)
        source = parts[1].strip() if len(parts) > 1 else ""

        if not source:
            await self._respond(
                event,
                lang.get(
                    "commands.clone_usage",
                    "**Usage:** `.clone <git_url_or_path>`",
                ),
            )
            return

        progress_msg = lang.get(
            "commands.clone_progress",
            "Cloning plugin from `{url}`...",
        ).format(url=source)
        await self._respond(event, progress_msg)

        try:
            res = await plugin_service.install_plugin(source)
            name = res.get("name", "plugin")
            version = res.get("version", "?")
            success_msg = lang.get(
                "commands.clone_success",
                "Plugin **{name}** (v{version}) successfully installed!",
            ).format(name=name, version=version)
            await self._respond(event, success_msg)
        except Exception as e:
            err_msg = lang.get(
                "commands.clone_error",
                "Failed to install plugin: {error}",
            ).format(error=str(e))
            await self._respond(event, err_msg)
