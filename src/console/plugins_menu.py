#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console plugin management workflows."""

import json
import os
import shutil
import subprocess
import tempfile
from src.console.input_helper import InputHelper
from src.services.database_service import Colors, print_color
from src.services.logger import log
from src.services.plugin_scaffold import create_plugin_scaffold
from src.services.translator import lang


class PluginsMenu:
    """Terminal menu for discovering, enabling, and managing plugins."""

    def __init__(self, bot):
        self.bot = bot
        self.plugin_service = getattr(bot, "plugin_service", None)

    async def show_menu(self):
        """Displays plugins submenu loop."""
        while True:
            print_color(f"\n{lang.get('messages.plugins_menu', 'PLUGIN MANAGEMENT')}", Colors.MAGENTA)
            print(lang.get("messages.plugins_list_all", "1. List all plugins"))
            print(lang.get("messages.plugins_list_loaded", "2. List loaded"))
            print(lang.get("messages.plugins_enable_all", "3. 🟢 Enable all"))
            print(lang.get("messages.plugins_disable_all", "4. 🔴 Disable all"))
            print(lang.get("messages.plugins_enable", "5. 🟢 Enable plugin"))
            print(lang.get("messages.plugins_disable", "6. 🔴 Disable plugin"))
            print(lang.get("messages.plugins_reload", "7. Reload plugin"))
            print(lang.get("messages.plugins_unload", "8. Unload plugin"))
            print(lang.get("messages.plugins_load", "9. Load plugin"))
            print(lang.get("messages.plugins_delete", "10. Delete plugin"))
            print(lang.get("messages.plugins_install", "11. Install plugin"))
            print(lang.get("messages.plugins_update", "12. Update plugin"))
            print(lang.get("messages.plugins_create", "13. Create plugin"))
            print(lang.get("messages.plugins_back", "0. Back"))

            choice = (await InputHelper.get_input(f"\n{lang.get('messages.select', 'Select: ')}")).strip()

            if choice == "1":
                await self._list_all()
            elif choice == "2":
                await self._list_loaded()
            elif choice == "3":
                await self._enable_all()
            elif choice == "4":
                await self._disable_all()
            elif choice == "5":
                await self._enable_plugin()
            elif choice == "6":
                await self._disable_plugin()
            elif choice == "7":
                await self._reload_plugin()
            elif choice == "8":
                await self._unload_plugin()
            elif choice == "9":
                await self._load_plugin()
            elif choice == "10":
                await self._delete_plugin()
            elif choice == "11":
                await self._install_plugin()
            elif choice == "12":
                await self._update_plugin()
            elif choice == "13":
                await self._create_plugin()
            elif choice == "0":
                break

    async def _list_all(self):
        if not self.plugin_service:
            print_color(lang.get("messages.plugin_service_unavailable", "Plugin service unavailable"), Colors.RED)
            return

        plugins = self.plugin_service.find_plugins()
        if not plugins:
            print_color(lang.get("messages.plugins_no_plugins", "No plugins available"), Colors.YELLOW)
            return

        loaded = self.plugin_service.get_loaded_plugins()
        term_width = min(shutil.get_terminal_size().columns - 2, 72)
        sep_thick = "═" * term_width
        sep_thin = "─" * term_width
        print_color(f"\n{lang.get('messages.plugins_heading_all', 'All plugins')}:", Colors.CYAN)
        print_color(sep_thick, Colors.CYAN)
        for name in plugins:
            status = "🟢" if name in loaded else "🔴"
            version = "?"
            desc = ""
            plugin_id = "?"
            author = "?"

            manifest_path = os.path.join("plugins", name, "manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    version = manifest.get("version", "?")
                    desc = manifest.get("description", "")
                    author = manifest.get("author", "?")
                    plugin_id = manifest.get("id", "?")
                except Exception:
                    pass

            print_color(f"{status} " + lang.get("messages.plugins_list_entry", "{name} v{version} by {author} - {desc}").format(name=name, version=version, author=author, desc=desc), Colors.YELLOW)
            print_color(f"ID: {plugin_id}", Colors.CYAN)
            print_color(sep_thin, Colors.CYAN)

    async def _list_loaded(self):
        if not self.plugin_service:
            print_color(lang.get("messages.plugin_service_unavailable", "Plugin service unavailable"), Colors.RED)
            return

        loaded = self.plugin_service.get_loaded_plugins()
        if not loaded:
            print_color(lang.get("messages.no_plugins", "No plugins loaded"), Colors.YELLOW)
            return

        term_width = min(shutil.get_terminal_size().columns - 2, 72)
        sep_thick = "═" * term_width
        sep_thin = "─" * term_width
        print_color(f"\n{lang.get('messages.plugins_heading_loaded', 'Loaded plugins')}:", Colors.CYAN)
        print_color(sep_thick, Colors.CYAN)
        for name in loaded:
            version = "?"
            desc = ""
            plugin_id = "?"
            author = "?"

            manifest_path = os.path.join("plugins", name, "manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    version = manifest.get("version", "?")
                    desc = manifest.get("description", "")
                    author = manifest.get("author", "?")
                    plugin_id = manifest.get("id", "?")
                except Exception:
                    pass

            print_color("🟢 " + lang.get("messages.plugins_list_entry", "{name} v{version} by {author} - {desc}").format(name=name, version=version, author=author, desc=desc), Colors.YELLOW)
            print_color(f"ID: {plugin_id}", Colors.CYAN)
            print_color(sep_thin, Colors.CYAN)

    async def _enable_all(self):
        if not self.plugin_service:
            return
        plugins = self.plugin_service.find_plugins()
        loaded = 0
        for p in plugins:
            if p not in self.plugin_service.get_loaded_plugins():
                if await self.plugin_service.load_plugin(p):
                    loaded += 1
        print_color(lang.get("messages.plugins_all_enabled", "🟢 All plugins enabled"), Colors.GREEN)

    async def _disable_all(self):
        if not self.plugin_service:
            return
        loaded = list(self.plugin_service.get_loaded_plugins())
        for p in loaded:
            await self.plugin_service.unload_plugin(p)
        print_color(lang.get("messages.plugins_all_disabled", "🔴 All plugins disabled"), Colors.YELLOW)

    async def _enable_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_enter_name", "Plugin name: "))).strip()
        if name and self.plugin_service:
            ok = await self.plugin_service.load_plugin(name)
            if ok:
                msg = lang.get("messages.plugins_enabled", "Plugin enabled: {name}").replace("{name}", name)
                print_color(f"🟢 {msg}", Colors.GREEN)
            else:
                print_color(lang.get("messages.plugins_enable_failed", "Failed to enable plugin '{name}'").format(name=name), Colors.RED)

    async def _disable_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_enter_name", "Plugin name: "))).strip()
        if name and self.plugin_service:
            ok = await self.plugin_service.unload_plugin(name)
            if ok:
                msg = lang.get("messages.plugins_disabled", "Plugin disabled: {name}").replace("{name}", name)
                print_color(f"🔴 {msg}", Colors.YELLOW)
            else:
                print_color(lang.get("messages.plugins_disable_failed", "Failed to disable plugin '{name}'").format(name=name), Colors.RED)

    async def _reload_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_enter_name", "Plugin name: "))).strip()
        if name and self.plugin_service:
            ok = await self.plugin_service.reload_plugin(name)
            if ok:
                msg = lang.get("messages.plugins_reloaded", "Plugin reloaded: {name}").replace("{name}", name)
                print_color(msg, Colors.GREEN)
            else:
                print_color(lang.get("messages.plugins_reload_failed", "Failed to reload plugin '{name}'").format(name=name), Colors.RED)

    async def _unload_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_enter_name", "Plugin name: "))).strip()
        if name and self.plugin_service:
            ok = await self.plugin_service.unload_plugin(name)
            if ok:
                msg = lang.get("messages.plugins_unloaded", "Plugin unloaded: {name}").replace("{name}", name)
                print_color(msg, Colors.GREEN)
            else:
                print_color(lang.get("messages.plugins_unload_failed", "Failed to unload plugin '{name}'").format(name=name), Colors.RED)

    async def _load_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_enter_name", "Plugin name: "))).strip()
        if name and self.plugin_service:
            ok = await self.plugin_service.load_plugin(name)
            if ok:
                msg = lang.get("messages.plugins_load_success", "Plugin loaded: {name}").replace("{name}", name)
                print_color(msg, Colors.GREEN)
            else:
                print_color(lang.get("messages.plugins_load_failed", "Failed to load plugin '{name}'").format(name=name), Colors.RED)

    async def _delete_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_enter_name", "Plugin name: "))).strip()
        if not name:
            return

        confirm = (await InputHelper.get_input(lang.get("messages.plugins_confirm_delete", "Are you sure you want to delete {name}? (y/n): ").replace("{name}", name))).strip().lower()
        if confirm == "y":
            if self.plugin_service and name in self.plugin_service.get_loaded_plugins():
                await self.plugin_service.unload_plugin(name)
            plugin_dir = os.path.join("plugins", name)
            if os.path.exists(plugin_dir):
                shutil.rmtree(plugin_dir)
                msg = lang.get("messages.plugins_deleted", "Plugin deleted: {name}").replace("{name}", name)
                print_color(msg, Colors.GREEN)
            else:
                print_color(lang.get("messages.plugins_folder_not_found", "Plugin folder '{path}' not found").format(path=plugin_dir), Colors.RED)

    async def _install_plugin(self):
        url = (await InputHelper.get_input(lang.get("messages.plugins_enter_url", "Folder path or Git URL: "))).strip()
        if not url:
            return
        print_color(lang.get("messages.plugins_installing", "Installing plugin..."), Colors.CYAN)
        if url.startswith("http://") or url.startswith("https://") or url.startswith("git@"):
            try:
                from src.services.plugin_service import PluginService, validate_plugin_source
                url = validate_plugin_source(url)
                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GIT_ALLOW_PROTOCOL"] = "https:http:ssh"
                network_args = PluginService._git_network_args(url)
                with tempfile.TemporaryDirectory() as tmpdir:
                    repo_dir = os.path.join(tmpdir, "repo")
                    cmd = ["git", "-c", "http.followRedirects=false", *network_args, "clone", "--depth", "1", "--single-branch", "--", url, repo_dir]
                    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120, env=env)
                    manifest_found = None
                    for root, dirs, files in os.walk(repo_dir):
                        if "manifest.json" in files and "plugin.py" in files:
                            manifest_found = root
                            break
                    if manifest_found:
                        with open(os.path.join(manifest_found, "manifest.json"), "r", encoding="utf-8") as f:
                            m_data = json.load(f)
                        p_name = m_data.get("name") or os.path.basename(url).replace(".git", "")
                        target_dir = os.path.join("plugins", p_name)
                        if os.path.exists(target_dir):
                            shutil.rmtree(target_dir)

                        if os.path.abspath(manifest_found) == os.path.abspath(repo_dir):
                            shutil.move(repo_dir, target_dir)
                        else:
                            shutil.copytree(
                                manifest_found,
                                target_dir,
                                ignore=shutil.ignore_patterns(".github", "__pycache__", "*.pyc"),
                            )
                            print_color(lang.get("messages.plugins_subdir_no_updates", "Plugin is located in a subdirectory; git updates are not available"), Colors.YELLOW)

                        print_color(lang.get("messages.plugins_installed_to", "Plugin installed to plugins/{path}").format(path=p_name), Colors.GREEN)
                        if self.plugin_service:
                            await self.plugin_service.load_plugin(p_name)
                    else:
                        print_color(lang.get("messages.plugins_manifest_missing", "manifest.json or plugin.py not found in repository"), Colors.RED)
            except Exception as e:
                print_color(lang.get("messages.plugins_install_folder_error", "Installation failed: {error}").format(error=e), Colors.RED)
        elif os.path.isdir(url):
            p_name = os.path.basename(os.path.abspath(url))
            target_dir = os.path.join("plugins", p_name)
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            shutil.copytree(
                url,
                target_dir,
                ignore=shutil.ignore_patterns(".github", "__pycache__", "*.pyc"),
            )
            print_color(lang.get("messages.plugins_copied_to", "Plugin copied to plugins/{path}").format(path=p_name), Colors.GREEN)
            if self.plugin_service:
                await self.plugin_service.load_plugin(p_name)
        else:
            print_color(lang.get("messages.plugins_invalid_source", "Invalid path or URL"), Colors.RED)

    async def _update_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_update_enter", "Plugin name to update: "))).strip()
        if not name:
            return

        if not self.plugin_service:
            print_color(lang.get("messages.plugin_service_unavailable", "Plugin service unavailable"), Colors.RED)
            return

        print_color(
            lang.get("messages.plugins_update_start", "Updating {name}...").format(name=name),
            Colors.CYAN,
        )

        try:
            result = await self.plugin_service.update_plugin(name)
        except Exception as e:
            msg = lang.get("messages.plugins_update_error", "Error: {error}").format(error=str(e))
            print_color(msg, Colors.RED)
            return

        if result["status"] == "newer_installed":
            msg = lang.get("messages.plugins_update_newer_installed", "Newer version installed: v{old} > v{new}").format(
                old=result["old_version"], new=result["new_version"]
            )
            print_color(msg, Colors.YELLOW)
            confirm = await InputHelper.get_input(
                lang.get("messages.plugins_update_downgrade_confirm", "Downgrade? (y/n): ")
            )
            if confirm.strip().lower() != "y":
                return
            try:
                result = await self.plugin_service.update_plugin(name, force=True)
            except Exception as e:
                msg = lang.get("messages.plugins_update_error", "Error: {error}").format(error=str(e))
                print_color(msg, Colors.RED)
                return

        if result["status"] == "no_changes":
            msg = lang.get("messages.plugins_update_no_changes", "{name} is already up to date (v{version})").format(
                name=name, version=result["version"]
            )
            print_color(msg, Colors.YELLOW)
        elif result["status"] == "updated":
            msg = lang.get("messages.plugins_update_success", "{name} updated: v{old} -> v{new}").format(
                name=name, old=result["old_version"], new=result["new_version"]
            )
            print_color(msg, Colors.GREEN)
        else:
            msg = lang.get("messages.plugins_update_error", "Error: {error}").format(error=result.get("error", "Unknown error"))
            print_color(msg, Colors.RED)

    async def _create_plugin(self):
        name = (await InputHelper.get_input(lang.get("messages.plugins_create_name", "Plugin name: "))).strip()
        if not name:
            return
        author = (await InputHelper.get_input(lang.get("messages.plugins_create_author", "Author: "))).strip() or lang.get("messages.plugins_create_default_author", "User")
        desc = (await InputHelper.get_input(lang.get("messages.plugins_create_description", "Description: "))).strip() or lang.get("messages.plugins_create_default_description", "My plugin")
        category = (await InputHelper.get_input(lang.get("messages.plugins_create_category", "Category (General, Utilities, Admin): "))).strip() or lang.get("messages.plugins_create_default_category", "General")
        web_page_answer = (await InputHelper.get_input(
            lang.get("messages.plugins_create_web_page", "Create a page in the web interface? (y/N): ")
        )).strip().lower()
        create_web_page = web_page_answer in {"y", "yes", "т", "так", "д", "да"}

        try:
            created = create_plugin_scaffold(
                name=name,
                author=author,
                description=desc,
                category=category,
                create_web_page=create_web_page,
            )
        except FileExistsError:
            print_color(lang.get("messages.plugins_create_exists", "Plugin '{name}' already exists").format(name=name), Colors.RED)
            return
        except ValueError as exc:
            print_color(lang.get("messages.plugins_create_error", "Creation error: {error}").format(error=exc), Colors.RED)
            return
        except Exception as exc:
            log.exception(f"Failed to create plugin '{name}'")
            print_color(lang.get("messages.plugins_create_error", "Creation error: {error}").format(error=exc), Colors.RED)
            return

        log.info(f"Plugin '{name}' created from console (web_page={create_web_page})")
        print_color(lang.get("messages.plugins_create_success", "Plugin '{name}' created!").format(name=name), Colors.GREEN)
        print_color(lang.get("messages.plugins_create_id", "ID: {id}").format(id=created["id"]), Colors.CYAN)
        print_color(lang.get("messages.plugins_create_path", "plugins/{path}").format(path=name), Colors.CYAN)
        if create_web_page:
            print_color(
                lang.get("messages.plugins_create_web_page_path", "   Web page: /plugin/{path}/overview").format(path=name),
                Colors.CYAN,
            )

        if self.plugin_service:
            if await self.plugin_service.load_plugin(name):
                print_color(lang.get("messages.plugins_load_success", "Plugin loaded: {name}").format(name=name), Colors.GREEN)
            else:
                print_color(lang.get("messages.plugins_create_restart", "Restart the bot to activate it"), Colors.YELLOW)
