#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main console menu dispatcher."""

from pathlib import Path
from src.console.input_helper import InputHelper
from src.console.plugins_menu import PluginsMenu
from src.console.settings_menu import SettingsMenu
from src.console.stats_menu import StatsMenu
from src.console.tasks_menu import TasksMenu
from src.services.database_service import Colors, print_color
from src.services.export_import_service import ExportImportService
from src.services.logger import log
from src.services.translator import format_language_choices, language_from_choice, lang


class Menu:
    """Dispatches user choices from top-level terminal menu."""

    def __init__(self, bot):
        self.bot = bot
        self.tasks_menu = TasksMenu(bot)
        self.plugins_menu = PluginsMenu(bot)
        self.settings_menu = SettingsMenu(bot)

    async def show_main_menu(self):
        """Runs the main CLI interactive navigation loop."""
        while True:
            print_color(f"\n{lang.get('menu.title', '=== MAIN MENU ===')}", Colors.MAGENTA)
            print(lang.get("menu.add_task", "1. Add task"))
            print(lang.get("menu.list_tasks", "2. List tasks"))
            print(lang.get("menu.start_task", "3. ▶ Start task"))
            print(lang.get("menu.stop_task", "4. Ⅱ Stop task"))
            print(lang.get("menu.edit_task", "5. Edit task"))
            print(lang.get("menu.activate", "6. 🟢 Activate"))
            print(lang.get("menu.deactivate", "7. 🔴 Deactivate"))
            print(lang.get("menu.delete", "8. Delete task"))
            print(lang.get("menu.stop_all", "9. Stop all"))
            print(lang.get("menu.task_info", "10. Task info"))
            print(lang.get("menu.settings", "11. Settings"))
            print(lang.get("menu.plugins", "12. Plugin management"))
            print(lang.get("menu.web_interface", "13. Web interface (on/off)"))
            print(lang.get("menu.exit", "0. Exit"))

            choice = (await InputHelper.get_input(f"\n{lang.get('messages.select', 'Select: ')}")).strip()
            if choice:
                log.info(f"Console selection: {choice}")

            if choice == "1":
                await self.tasks_menu.add_task()
            elif choice == "2":
                await self.tasks_menu.list_tasks()
            elif choice == "3":
                await self.tasks_menu.start_task()
            elif choice == "4":
                await self.tasks_menu.stop_task()
            elif choice == "5":
                await self.tasks_menu.edit_task()
            elif choice == "6":
                await self.tasks_menu.activate_task()
            elif choice == "7":
                await self.tasks_menu.deactivate_task()
            elif choice == "8":
                await self.tasks_menu.delete_task()
            elif choice == "9":
                if hasattr(self.bot, "task_service") and self.bot.task_service:
                    await self.bot.task_service.stop_all()
                print_color(lang.get("messages.all_stopped", "All tasks stopped"), Colors.GREEN)
            elif choice == "10":
                await self.tasks_menu.show_task_detail()
            elif choice == "11":
                await self.settings_menu.show_menu()
            elif choice == "12":
                await self.plugins_menu.show_menu()
            elif choice == "13":
                await self.settings_menu.toggle_web_interface()
            elif choice == "0":
                if hasattr(self.bot, "task_service") and self.bot.task_service:
                    await self.bot.task_service.stop_all()
                print_color(lang.get("messages.goodbye", "Goodbye!"), Colors.GREEN)
                break

    async def _export_tasks(self):
        """Exports tasks to JSON backup file."""
        filename = (await InputHelper.get_input(lang.get("messages.enter_filename", "Filename (tasks_backup.json): "))).strip()
        if not filename:
            filename = "tasks_backup.json"
        count = ExportImportService.export_tasks(self.bot.db, filename)
        log.info(f"Tasks exported from console: {count} to {filename}")
        if count > 0:
            msg = lang.get("export.success", "Exported {count} tasks to {filename}").replace("{count}", str(count)).replace("{filename}", filename)
            print_color(msg, Colors.GREEN)
        else:
            print_color(lang.get("export.error", "Export error"), Colors.RED)

    async def _import_tasks(self):
        """Imports tasks from JSON backup file."""
        filename = (await InputHelper.get_input(lang.get("messages.enter_filename", "Filename (tasks_backup.json): "))).strip()
        if not filename:
            filename = "tasks_backup.json"
        count = ExportImportService.import_tasks(self.bot.db, filename)
        log.info(f"Tasks imported from console: {count} from {filename}")
        if count > 0:
            msg = lang.get("import.success", "Imported {count} tasks from {filename}").replace("{count}", str(count)).replace("{filename}", filename)
            print_color(msg, Colors.GREEN)
        else:
            print_color(lang.get("import.error", "Import error"), Colors.RED)

    async def _change_language(self):
        """Changes active UI language."""
        current_l = lang.get_lang().upper()
        curr_msg = lang.get("messages.current_language", "Current language: {lang}").replace("{lang}", current_l)
        print_color(f"\n{curr_msg}", Colors.CYAN)
        print(format_language_choices())

        opt = (await InputHelper.get_input(lang.get("messages.select_language", "Select: "))).strip()
        new_lang = language_from_choice(opt)
        if new_lang:
            lang.set_language(new_lang)

            plugin_service = getattr(self.bot, "plugin_service", None)
            if plugin_service:
                plugin_service.set_language(new_lang)

            try:
                env_path = Path(".env")
                lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
                replaced = False
                for index, line in enumerate(lines):
                    if line.strip().startswith("LANGUAGE="):
                        lines[index] = f"LANGUAGE={new_lang}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"LANGUAGE={new_lang}")
                env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except Exception as e:
                print_color(lang.get("messages.language_config_error", "Failed to persist language to .env: {error}").replace("{error}", str(e)), Colors.YELLOW)

            log.info(f"Language changed from console: {new_lang}")
            print_color(f"{lang.get('messages.language_changed', 'Language changed to {lang}').replace('{lang}', new_lang.upper())}", Colors.GREEN)
        else:
            print_color(lang.get("messages.wrong_choice", "Invalid selection"), Colors.RED)
