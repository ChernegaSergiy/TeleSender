#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tasks overview command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class TasksCommand(BaseCommand):
    """Lists current scheduled tasks and their execution states."""

    async def execute(self, event) -> None:
        tasks = self.db.get_all()
        if not tasks:
            await self._respond(event, lang.get("messages.no_tasks", "No tasks found"))
            return

        running_tasks = self.task_service.running if self.task_service else {}
        scheduler = self.scheduler or getattr(self.bot, "scheduler_service", None)

        task_list = ""
        for t in tasks[:10]:
            tid = t[0]
            chat = t[1]
            cfg = t[5]
            active = t[6]
            total = t[7]

            status = "🟢" if active else "🔴"
            running = "▶" if tid in running_tasks else "Ⅱ"
            interval_display = scheduler.format_interval(cfg) if scheduler else "60s"

            times_word = (
                scheduler.pluralize(
                    total,
                    lang.get("messages.time_once", "time"),
                    lang.get("messages.time_few", "times"),
                    lang.get("messages.time_many", "times"),
                )
                if scheduler
                else "times"
            )

            task_list += f"{status}{running} #{tid} | {chat[:25]} | {interval_display} | {total} {times_word}\n"

        await self._respond(event,
            lang.get("commands.tasks", "**Tasks:**\n{list}").format(list=task_list)
        )
