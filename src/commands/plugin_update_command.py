#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update one or all installed plugins from their Git repositories."""

import html
import logging
from src.commands.base_command import BaseCommand
from src.services.translator import lang

logger = logging.getLogger("telesender.commands.update")


class PluginUpdateCommand(BaseCommand):
    """Updates one or all plugins from their Git repositories.

    Aliases: .update, .pupdate, .update_plugin
    Usage:   .update [<plugin_name>|all]

    When a newer version is already installed, shows inline Y/N buttons
    to confirm whether to force downgrade to the remote version.
    """

    async def execute(self, event) -> None:
        plugin_service = getattr(self.bot, "plugin_service", None)
        if not plugin_service:
            await self._respond(event, lang.get("messages.no_plugins", "Plugin service unavailable"))
            return

        text = event.raw_text or ""
        parts = text.split()
        target = parts[1].strip() if len(parts) > 1 else ""

        # --- Update a specific plugin ---
        if target and target.lower() != "all":
            all_plugins = plugin_service.find_plugins()
            if target not in all_plugins:
                await self._respond(
                    event,
                    lang.get("commands.update_not_found", "Plugin **{name}** not found.").format(name=target),
                )
                return

            await self._respond(
                event,
                lang.get("commands.update_progress", "Updating plugin **{name}**...").format(name=target),
            )

            res = await plugin_service.update_plugin(target)
            status = res.get("status")

            if status == "updated":
                msg = lang.get(
                    "commands.update_success",
                    "Plugin **{name}** updated: v{old} -> v{new}",
                ).format(name=target, old=res.get("old_version", "?"), new=res.get("new_version", "?"))
                await self._respond(event, msg)

            elif status == "no_changes":
                msg = lang.get(
                    "commands.update_no_changes",
                    "Plugin **{name}** is already up to date (v{version})",
                ).format(name=target, version=res.get("version", "?"))
                await self._respond(event, msg)

            elif status == "newer_installed":
                await self._ask_force_update(event, plugin_service, target, res)

            else:
                msg = lang.get(
                    "commands.update_error",
                    "Failed to update plugin **{name}**: {error}",
                ).format(name=target, error=res.get("error", "Unknown error"))
                await self._respond(event, msg)

            return

        # --- Update all plugins ---
        all_plugins = plugin_service.find_plugins()
        if not all_plugins:
            await self._respond(event, lang.get("commands.update_no_plugins", "No plugins installed to update."))
            return

        await self._respond(
            event,
            lang.get("commands.update_all_progress", "Checking updates for all plugins..."),
        )

        results = []
        for name in all_plugins:
            res = await plugin_service.update_plugin(name)
            s = res.get("status")
            if s == "updated":
                line = f"• **{name}**: v{res.get('old_version', '?')} -> v{res.get('new_version', '?')}"
            elif s == "no_changes":
                line = f"• **{name}**: v{res.get('version', '?')} (up to date)"
            elif s == "newer_installed":
                line = f"• **{name}**: v{res.get('old_version', '?')} > v{res.get('new_version', '?')} (newer installed)"
            else:
                line = f"• **{name}**: {res.get('error', 'Error')}"
            results.append(line)

        header = lang.get("commands.update_summary_title", "**Plugin update results:**")
        msg = f"{header}\n\n" + "\n".join(results)
        await self._respond(event, msg)

    async def _ask_force_update(self, event, plugin_service, name: str, res: dict) -> None:
        """Send an inline form with y/n buttons to confirm force downgrade."""
        old_v = res.get("old_version", "?")
        new_v = res.get("new_version", "?")

        # HTML-safe format for inline bot
        safe_name = html.escape(name)
        confirm_html = lang.get(
            "commands.update_newer_confirm_html",
            "Plugin <b>{name}</b>: installed v{old} &gt; remote v{new}\n\nForce downgrade to remote version?",
        ).format(name=safe_name, old=old_v, new=new_v)

        confirm_text_plain = lang.get(
            "commands.update_newer_confirm",
            "Plugin **{name}**: installed v{old} > remote v{new}\n\nForce downgrade to remote version?",
        ).format(name=name, old=old_v, new=new_v)

        inline = getattr(self.bot, "inline", None)
        if inline and inline.is_ready:
            async def on_yes(call):
                try:
                    await call.answer()
                    r = await plugin_service.update_plugin(name, force=True)
                    s = r.get("status")
                    if s == "updated":
                        text = lang.get(
                            "commands.update_rollback_success_html",
                            "Plugin <b>{name}</b> rolled back: v{old} -> v{new}",
                        ).format(name=safe_name, old=r.get("old_version", "?"), new=r.get("new_version", "?"))
                    else:
                        text = lang.get(
                            "commands.update_error_html",
                            "Failed to update plugin <b>{name}</b>: {error}",
                        ).format(name=safe_name, error=html.escape(str(r.get("error", "Unknown error"))))
                    await call.edit(text, reply_markup=[])
                except Exception as e:
                    logger.error("Error in on_yes callback for %s: %s", name, e, exc_info=True)
                    try:
                        await call.edit(f"Error: {html.escape(str(e))}", reply_markup=[])
                    except Exception:
                        pass

            async def on_no(call):
                try:
                    await call.answer()
                    cancel_text = lang.get(
                        "commands.update_newer_cancelled_html",
                        "Downgrade of <b>{name}</b> cancelled.",
                    ).format(name=safe_name)
                    await call.edit(cancel_text, reply_markup=[])
                except Exception as e:
                    logger.error("Error in on_no callback for %s: %s", name, e, exc_info=True)
                    try:
                        await call.edit(f"Cancelled: {html.escape(str(name))}", reply_markup=[])
                    except Exception:
                        pass

            await inline.form(
                confirm_html,
                event,
                reply_markup=[
                    [
                        {"text": "y", "callback": on_yes},
                        {"text": "n", "callback": on_no},
                    ]
                ],
            )
        else:
            await self._respond(event, confirm_text_plain)
