#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console settings submenu: cache, stats, export/import, language."""

from pathlib import Path

from src.console.input_helper import InputHelper
from src.console.stats_menu import StatsMenu
from src.config.config import config
from src.services.database_service import Colors, print_color
from src.services.export_import_service import ExportImportService
from src.services.logger import log
from src.services.translator import format_language_choices, language_from_choice, lang


class SettingsMenu:
    def __init__(self, bot):
        self.bot = bot
        self.stats_menu = StatsMenu(bot)

    async def show_menu(self):
        while True:
            print_color(f"\n{lang.get('settings.title', '=== SETTINGS ===')}", Colors.MAGENTA)
            print(lang.get("menu.clear_cache", "1. Clear media cache"))
            print(lang.get("menu.stats", "2. Statistics"))
            print(lang.get("menu.export", "3. Export tasks"))
            print(lang.get("menu.import", "4. Import tasks"))
            print(lang.get("menu.language", "5. Change language"))
            print(lang.get("menu.back", "0. Back"))

            choice = (await InputHelper.get_input(f"\n{lang.get('messages.select', 'Select: ')}")).strip()
            if choice:
                log.info(f"Settings selection: {choice}")

            if choice == "1":
                if hasattr(self.bot, "media_service") and self.bot.media_service:
                    cleared = self.bot.media_service.clear_media_cache()
                    log.info(f"Media cache cleared from console: {cleared} item(s)")
                    print_color(lang.get("messages.cache_cleared", "Cleared {count} cached items").replace("{count}", str(cleared)), Colors.GREEN)
            elif choice == "2":
                self.stats_menu.show_stats()
            elif choice == "3":
                await self._export_tasks()
            elif choice == "4":
                await self._import_tasks()
            elif choice == "5":
                await self._change_language()
            elif choice == "0":
                break

    async def _export_tasks(self):
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
                self._persist_env("LANGUAGE", new_lang)
            except Exception as e:
                print_color(lang.get("messages.language_config_error", "Failed to persist language to .env: {error}").replace("{error}", str(e)), Colors.YELLOW)

            log.info(f"Language changed from console: {new_lang}")
            print_color(f"{lang.get('messages.language_changed', 'Language changed to {lang}').replace('{lang}', new_lang.upper())}", Colors.GREEN)
        else:
            print_color(lang.get("messages.wrong_choice", "Invalid selection"), Colors.RED)

    @staticmethod
    def _persist_env(key: str, value: str):
        """Writes or replaces a key in .env while keeping other lines intact."""
        env_path = Path(".env")
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        prefix = f"{key}="
        for index, line in enumerate(lines):
            if line.strip().startswith(prefix):
                lines[index] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def toggle_web_interface(self):
        """Starts or stops the web dashboard from the console."""
        web_server = getattr(self.bot, "web_server", None)
        if web_server is None:
            print_color(lang.get("messages.web_unavailable", "Web interface is not available"), Colors.RED)
            return

        if web_server.running:
            await web_server.stop()
            log.info("Web interface stopped from console")
            self._persist_web_state("false")
            print_color(lang.get("messages.web_stopped", "Web interface disabled"), Colors.GREEN)
            return

        try:
            await web_server.start()
        except Exception as e:
            log.error(f"Failed to start web interface from console: {e}")
            msg = lang.get("messages.web_start_error", "Failed to start web interface: {error}").replace("{error}", str(e))
            print_color(msg, Colors.RED)
            return

        log.info("Web interface started from console")
        self._persist_web_state("true")
        url = config.web_url
        print_color(lang.get("messages.web_started", "Web interface enabled: {url}").replace("{url}", url), Colors.GREEN)

    def _persist_web_state(self, value: str):
        """Saves the web interface state to .env, warning if it cannot be written."""
        try:
            self._persist_env("WEB_ENABLED", value)
        except Exception as e:
            msg = lang.get("messages.web_config_error", "Web interface state changed, but .env could not be updated: {error}").replace("{error}", str(e))
            print_color(msg, Colors.YELLOW)
