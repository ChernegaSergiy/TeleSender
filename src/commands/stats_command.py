#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Statistics inspection command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class StatsCommand(BaseCommand):
    """Replies with summary statistics about tasks and delivery counts."""

    async def execute(self, event) -> None:
        tasks = self.db.get_all()
        if not tasks:
            await self._respond(event, lang.get("messages.no_tasks", "No tasks found"))
            return

        running_tasks = self.task_service.running if self.task_service else {}

        total_sent = sum(t[7] for t in tasks)
        active = sum(1 for t in tasks if t[6])
        with_media = sum(1 for t in tasks if t[3])
        once = sum(1 for t in tasks if t[9])
        rotation = sum(1 for t in tasks if t[12])

        msg = lang.get(
            "commands.stats",
            "**Statistics**\n\n"
            "Tasks total: {count}\n"
            "🟢 Active: {active}\n"
            "Sent: {sent}\n"
            "▶ Running: {running}\n"
            "With media: {media}\n"
            "One-time: {once}\n"
            "With rotation: {rotation}",
        ).format(
            count=len(tasks),
            active=active,
            sent=total_sent,
            running=len(running_tasks),
            media=with_media,
            once=once,
            rotation=rotation,
        )

        await self._respond(event, msg)
