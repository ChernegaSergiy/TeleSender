#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console statistics viewer."""

import shutil
from src.services.database_service import Colors, print_color
from src.services.translator import lang


class StatsMenu:
    """Renders formatted execution analytics and delivery metrics in terminal."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.task_service = getattr(bot, "task_service", None)

    def show_stats(self):
        """Prints statistical overview."""
        tasks = self.db.get_all()
        if not tasks:
            print_color(lang.get("messages.no_tasks", "No tasks found"), Colors.YELLOW)
            return

        term_width = min(shutil.get_terminal_size().columns - 2, 70)
        line = "=" * term_width

        total_sent = sum(t[7] for t in tasks)
        active_tasks = sum(1 for t in tasks if t[6])
        running_tasks = len(self.task_service.running) if self.task_service else 0
        with_media = sum(1 for t in tasks if t[3])
        once_tasks = sum(1 for t in tasks if t[9])
        rotation_tasks = sum(1 for t in tasks if t[12])

        print_color(f"\n{line}", Colors.CYAN)
        print_color(lang.get("menu.stats", "Statistics"), Colors.MAGENTA)
        print_color(f"{line}", Colors.CYAN)

        print(f"\n{lang.get('stats.total', 'General')}:")
        print(f"• {lang.get('stats.total_tasks', 'Total tasks')}: {len(tasks)}")
        print(f"• {lang.get('stats.active_tasks', 'Active tasks')}: {active_tasks}")
        print(f"• {lang.get('stats.running_now', 'Running now')}: {running_tasks}")
        print(f"• {lang.get('stats.total_sent', 'Total sent')}: {total_sent}")

        print(f"\n{lang.get('stats.media', 'Media')}:")
        print(f"• {lang.get('stats.with_media', 'Tasks with media')}: {with_media}")

        print(f"\n{lang.get('stats.special', 'Special')}:")
        print(f"• {lang.get('stats.once_tasks', 'One-time tasks')}: {once_tasks}")
        print(f"• {lang.get('stats.rotation_tasks', 'Rotation tasks')}: {rotation_tasks}")

        print_color(f"{line}\n", Colors.CYAN)
