#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Service wrapping plugin loader and registry from core."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
from core import PluginLoader, PluginRegistry
from core.localization import CoreLocalization
from src.config.config import config
from src.services.translator import lang

PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PluginService:
    """Manages plugin discovery, lifecycle, and handler mappings."""

    def __init__(self, client=None, db=None, bot=None):
        self.loader = PluginLoader(client=client, db=db, bot=bot)
        self.registry = PluginRegistry()
        self.pages = self.loader.pages
        self.localization = CoreLocalization()
        self.logger = logging.getLogger("PluginService")
        # Plugin lifecycle mutates shared registries, so serialize it.
        self._lifecycle_lock = asyncio.Lock()
        # Never run two updates for the same service at once.
        self._update_lock = asyncio.Lock()

        if config.language:
            self.localization.set_language(config.language)
            self.loader.set_language(config.language)

    def set_dependencies(self, client=None, db=None, bot=None):
        """Updates runtime dependencies for plugin subsystem."""
        if client:
            self.loader.set_client(client)
        if db:
            self.loader.set_db(db)
        if bot:
            self.loader.set_bot(bot)

    def set_language(self, lang_code: str):
        """Updates localization language for all plugins."""
        self.localization.set_language(lang_code)
        self.loader.set_language(lang_code)

    async def load_all(self) -> int:
        """Loads all available plugins from disk."""
        return await self.loader.load_all_plugins()

    def get_loaded_plugins(self) -> List[str]:
        return self.loader.get_loaded_plugins()

    def find_plugins(self) -> List[str]:
        return self.loader.find_plugins()

    def get_command(self, cmd_name: str):
        return self.registry.get_command(cmd_name)

    def get_watchers(self):
        return self.registry.get_watchers()

    def get_plugin_instance(self, plugin_name: str):
        return self.loader.get_plugin_instance(plugin_name)

    def get_plugin_metadata(self, plugin_name: str):
        return self.registry.get_plugin(plugin_name)

    async def load_plugin(self, name: str) -> bool:
        async with self._lifecycle_lock:
            ok = await self.loader.load_plugin(name)
        if ok:
            self._invalidate_web_caches()
        return ok

    async def unload_plugin(self, name: str) -> bool:
        async with self._lifecycle_lock:
            ok = await self.loader.unload_plugin(name)
        if ok:
            self._invalidate_web_caches()
        return ok

    async def reload_plugin(self, name: str) -> bool:
        async with self._lifecycle_lock:
            ok = await self.loader.reload_plugin(name)
        if ok:
            self._invalidate_web_caches()
        return ok

    def _invalidate_web_caches(self) -> None:
        """Drops cached web pages and menus after plugin changes."""
        bot = getattr(self.loader, "bot", None)
        manager = getattr(bot, "web_server", None) if bot else None
        server = getattr(manager, "server", None) if manager else None
        invalidate = getattr(server, "invalidate_caches", None)
        if not callable(invalidate):
            return
        try:
            invalidate()
        except Exception:
            self.logger.debug("Could not invalidate web caches", exc_info=True)

    def _run_git_sync(self, plugin_path: str, args: List[str], timeout: int = 120):
        git_bin = shutil.which("git")
        if not git_bin:
            return None

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        command = [git_bin, "-C", plugin_path, *args]

        try:
            return subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr=f"Timeout after {timeout}s",
            )

    async def _run_git(self, plugin_path: str, args: List[str], timeout: int = 120):
        """Runs git in a worker thread so the event loop stays responsive."""
        return await asyncio.to_thread(
            self._run_git_sync, plugin_path, args, timeout
        )

    @staticmethod
    def _git_error(result) -> str:
        if result is None:
            return lang.get("messages.plugins_git_not_found", "Git is not installed")
        stderr = (result.stderr or "").strip()
        if stderr:
            return stderr.splitlines()[-1]
        stdout = (result.stdout or "").strip()
        if stdout:
            return stdout.splitlines()[-1]
        return lang.get("messages.plugins_error_git_unknown", "Unknown git error")

    @staticmethod
    def compare_versions(v1: str, v2: str) -> int:
        """Returns 1 if v1 > v2, 0 if equal, -1 if v1 < v2."""

        def parse(v):
            v = v.lstrip("vV")
            result = []
            for part in v.split("."):
                num = ""
                for ch in part:
                    if ch.isdigit():
                        num += ch
                    else:
                        break
                result.append(int(num) if num else 0)
            return result

        p1 = parse(v1)
        p2 = parse(v2)
        max_len = max(len(p1), len(p2))
        p1 += [0] * (max_len - len(p1))
        p2 += [0] * (max_len - len(p2))

        if p1 > p2:
            return 1
        if p1 < p2:
            return -1
        return 0

    @staticmethod
    def migrate_config(old_config: dict, new_config: dict, new_version: str) -> dict:
        """Merges user configuration with defaults from the new plugin version."""
        merged = {}
        for key, new_value in new_config.items():
            if key == "_version":
                continue
            merged[key] = old_config.get(key, new_value)
        merged["_version"] = new_version
        return merged

    async def update_plugin(self, name: str, force: bool = False) -> Dict[str, Any]:
        """Updates a plugin, one update at a time."""
        async with self._update_lock:
            result = await self._update_plugin_locked(name, force)
        if result.get("status") == "updated":
            self._invalidate_web_caches()
        return result

    async def _update_plugin_locked(self, name: str, force: bool = False) -> Dict[str, Any]:
        """Updates a plugin from its git repository using git pull."""
        plugin_path = os.path.join(self.loader.plugins_dir, name)
        manifest_path = os.path.join(plugin_path, "manifest.json")

        if not os.path.isfile(manifest_path):
            return {"status": "error", "error": lang.get("messages.plugins_error_no_manifest", "No manifest.json: {name}").format(name=name)}

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                old_manifest = json.load(f)
        except Exception as e:
            return {"status": "error", "error": lang.get("messages.plugins_error_invalid_manifest", "Invalid manifest: {error}").format(error=e)}

        old_version = old_manifest.get("version", "0.0.0")
        repo_url = old_manifest.get("repository") or old_manifest.get("source")

        if not shutil.which("git"):
            return {"status": "error", "error": lang.get("messages.plugins_git_not_found", "Git is not installed")}

        has_git = os.path.isdir(os.path.join(plugin_path, ".git"))

        if not repo_url and has_git:
            remote_url = await self._run_git(plugin_path, ["remote", "get-url", "origin"])
            if remote_url and remote_url.returncode == 0:
                repo_url = remote_url.stdout.strip()

        if not repo_url:
            return {"status": "error", "error": lang.get("messages.plugins_error_no_repository", "No repository in manifest: {name}").format(name=name)}

        if not has_git:
            return await self._adopt_legacy_plugin(name, plugin_path, repo_url, old_version, force)

        remote = await self._run_git(plugin_path, ["remote", "get-url", "origin"])
        if remote is None:
            return {"status": "error", "error": lang.get("messages.plugins_git_not_found", "Git is not installed")}

        if remote.returncode == 0:
            if remote.stdout.strip() != repo_url:
                result = await self._run_git(plugin_path, ["remote", "set-url", "origin", repo_url])
                if result.returncode != 0:
                    return {"status": "error", "error": self._git_error(result)}
        else:
            result = await self._run_git(plugin_path, ["remote", "add", "origin", repo_url])
            if result.returncode != 0:
                return {"status": "error", "error": self._git_error(result)}

        result = await self._run_git(plugin_path, ["fetch", "--prune", "origin"])
        if result.returncode != 0:
            return {"status": "error", "error": self._git_error(result)}

        upstream = await self._run_git(
            plugin_path,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        )
        if upstream.returncode != 0:
            return {
                "status": "error",
                "error": lang.get("messages.plugins_error_no_upstream", "No upstream branch configured for plugin repository"),
            }

        upstream_ref = upstream.stdout.strip()
        result = await self._run_git(plugin_path, ["show", f"{upstream_ref}:manifest.json"])
        if result.returncode != 0:
            return {"status": "error", "error": lang.get("messages.plugins_error_manifest_root", "manifest.json not found in repository root")}

        try:
            new_manifest = json.loads(result.stdout)
        except Exception as e:
            return {"status": "error", "error": lang.get("messages.plugins_error_invalid_remote_manifest", "Invalid remote manifest: {error}").format(error=e)}

        new_version = new_manifest.get("version", "0.0.0")
        comparison = self.compare_versions(new_version, old_version)

        if comparison == 0:
            return {"status": "no_changes", "version": old_version}

        if comparison < 0 and not force:
            return {
                "status": "newer_installed",
                "old_version": old_version,
                "new_version": new_version,
            }

        old_config = {}
        config_path = os.path.join(plugin_path, "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    old_config = json.load(f)
            except Exception:
                pass

        db_config = {}
        plugin_instance = self.get_plugin_instance(name)
        if plugin_instance and hasattr(plugin_instance, "_config_data"):
            db_config = plugin_instance._config_data

        tracked = await self._run_git(plugin_path, ["ls-files", "--error-unmatch", "config.json"])
        if tracked.returncode == 0:
            await self._run_git(plugin_path, ["checkout", "--", "config.json"])
        elif os.path.exists(config_path):
            os.remove(config_path)

        was_loaded = name in self.get_loaded_plugins()
        if was_loaded:
            await self.unload_plugin(name)

        try:
            if force:
                result = await self._run_git(plugin_path, ["reset", "--hard", upstream_ref])
            else:
                result = await self._run_git(plugin_path, ["pull", "--ff-only"])
            if result.returncode != 0:
                if was_loaded:
                    await self.load_plugin(name)
                return {"status": "error", "error": self._git_error(result)}

            with open(manifest_path, "r", encoding="utf-8") as f:
                updated_manifest = json.load(f)

            updated_version = updated_manifest.get("version", new_version)

            new_config = {}
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        new_config = json.load(f)
                except Exception:
                    pass

            merged_config = self.migrate_config(old_config, new_config, updated_version)
            for key in list(merged_config.keys()):
                if key == "_version":
                    continue
                if key in db_config:
                    merged_config[key] = db_config[key]
            merged_config["_version"] = updated_version

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(merged_config, f, indent=2, ensure_ascii=False)

            if was_loaded and not await self.load_plugin(name):
                return {"status": "error", "error": lang.get("messages.plugins_error_load_failed", "Failed to load plugin: {name}").format(name=name)}

            return {
                "status": "updated",
                "old_version": old_version,
                "new_version": updated_version,
            }
        except subprocess.TimeoutExpired:
            if was_loaded and name not in self.get_loaded_plugins():
                await self.load_plugin(name)
            return {"status": "error", "error": lang.get("messages.plugins_error_pull_timeout", "Git pull timeout (120s)")}
        except Exception as e:
            if was_loaded and name not in self.get_loaded_plugins():
                await self.load_plugin(name)
            return {"status": "error", "error": str(e)}

    async def _adopt_legacy_plugin(self, name, plugin_path, repo_url, old_version, force):
        """Converts a legacy copied plugin into a git checkout and updates it."""
        git_bin = shutil.which("git")
        if not git_bin:
            return {"status": "error", "error": lang.get("messages.plugins_git_not_found", "Git is not installed")}

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        was_loaded = name in self.get_loaded_plugins()

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                clone_target = os.path.join(tmpdir, "repo")
                backup = os.path.join(tmpdir, "backup")

                result = await asyncio.to_thread(
                    subprocess.run,
                    [git_bin, "clone", "--depth", "1", "--single-branch", repo_url, clone_target],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    return {"status": "error", "error": self._git_error(result)}

                clone_manifest = os.path.join(clone_target, "manifest.json")
                if not os.path.isfile(clone_manifest):
                    return {
                        "status": "error",
                        "error": lang.get("messages.plugins_error_manifest_root", "manifest.json not found in repository root"),
                    }

                try:
                    with open(clone_manifest, "r", encoding="utf-8") as f:
                        new_manifest = json.load(f)
                except Exception as e:
                    return {"status": "error", "error": lang.get("messages.plugins_error_invalid_remote_manifest", "Invalid remote manifest: {error}").format(error=e)}

                new_version = new_manifest.get("version", "0.0.0")
                comparison = self.compare_versions(new_version, old_version)

                if comparison < 0 and not force:
                    return {
                        "status": "newer_installed",
                        "old_version": old_version,
                        "new_version": new_version,
                    }

                old_config = {}
                config_path = os.path.join(plugin_path, "config.json")
                if os.path.exists(config_path):
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            old_config = json.load(f)
                    except Exception:
                        pass

                db_config = {}
                plugin_instance = self.get_plugin_instance(name)
                if plugin_instance and hasattr(plugin_instance, "_config_data"):
                    db_config = plugin_instance._config_data

                if was_loaded:
                    await self.unload_plugin(name)

                shutil.move(plugin_path, backup)

                try:
                    shutil.move(clone_target, plugin_path)

                    new_config = {}
                    if os.path.exists(config_path):
                        with open(config_path, "r", encoding="utf-8") as f:
                            new_config = json.load(f)

                    merged_config = self.migrate_config(old_config, new_config, new_version)
                    for key in list(merged_config.keys()):
                        if key == "_version":
                            continue
                        if key in db_config:
                            merged_config[key] = db_config[key]
                    merged_config["_version"] = new_version

                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(merged_config, f, indent=2, ensure_ascii=False)

                    if was_loaded and not await self.load_plugin(name):
                        raise RuntimeError(lang.get("messages.plugins_error_load_failed", "Failed to load plugin: {name}").format(name=name))
                except Exception as e:
                    if os.path.exists(plugin_path):
                        shutil.rmtree(plugin_path)
                    if os.path.exists(backup):
                        shutil.move(backup, plugin_path)
                    if was_loaded:
                        await self.load_plugin(name)
                    return {"status": "error", "error": str(e)}

                if comparison == 0:
                    return {"status": "no_changes", "version": old_version}

                return {
                    "status": "updated",
                    "old_version": old_version,
                    "new_version": new_version,
                }
        except subprocess.TimeoutExpired:
            if was_loaded and name not in self.get_loaded_plugins():
                await self.load_plugin(name)
            return {"status": "error", "error": lang.get("messages.plugins_error_clone_timeout", "Git clone timeout (120s)")}
        except Exception as e:
            if was_loaded and name not in self.get_loaded_plugins():
                await self.load_plugin(name)
            return {"status": "error", "error": str(e)}

    def _install_plugin_sync(self, source: str):
        source = source.strip()
        if not source:
            raise ValueError(lang.get("messages.plugins_invalid_source", "Invalid path or URL"))

        plugins_dir = self.loader.plugins_dir if self.loader else config.plugins_dir

        if source.startswith(("http://", "https://", "git@")):
            git_bin = shutil.which("git")
            if not git_bin:
                raise RuntimeError(lang.get("messages.plugins_git_not_found", "Git is not installed"))

            with tempfile.TemporaryDirectory() as tmpdir:
                repo_dir = os.path.join(tmpdir, "repo")
                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                result = subprocess.run(
                    [git_bin, "clone", "--depth", "1", "--single-branch", source, repo_dir],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                )
                if result.returncode != 0:
                    error = (result.stderr or result.stdout or "").strip()
                    raise RuntimeError(error or f"git clone failed ({result.returncode})")

                manifest_found = None
                for root, _dirs, files in os.walk(repo_dir):
                    if "manifest.json" in files and "plugin.py" in files:
                        manifest_found = root
                        break

                if not manifest_found:
                    raise ValueError(lang.get(
                        "messages.plugins_manifest_missing",
                        "manifest.json or plugin.py not found in repository",
                    ))

                manifest_path = os.path.join(manifest_found, "manifest.json")
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                name = str(manifest.get("name") or os.path.basename(source.rstrip("/"))).replace(".git", "")
                if not PLUGIN_NAME_RE.fullmatch(name):
                    raise ValueError(lang.get(
                        "web.errors.invalid_plugin_name",
                        "Invalid plugin name. Use only letters, digits, _ and -",
                    ))

                target_dir = os.path.join(plugins_dir, name)
                # Remove existing plugin directory to allow reinstall/overwrite
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

                return name, manifest.get("version", "?")

        if os.path.isdir(source):
            name = os.path.basename(os.path.abspath(source))
            if not PLUGIN_NAME_RE.fullmatch(name):
                raise ValueError(lang.get(
                    "web.errors.invalid_plugin_name",
                    "Invalid plugin name. Use only letters, digits, _ and -",
                ))
            target_dir = os.path.join(plugins_dir, name)
            # Remove existing plugin directory to allow reinstall/overwrite
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            shutil.copytree(
                source,
                target_dir,
                ignore=shutil.ignore_patterns(".github", "__pycache__", "*.pyc"),
            )
            version = "?"
            manifest_path = os.path.join(target_dir, "manifest.json")
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    version = json.load(f).get("version", "?")
            except Exception:
                pass
            return name, version

        raise ValueError(lang.get("messages.plugins_invalid_source", "Invalid path or URL"))

    async def install_plugin(self, source: str) -> Dict[str, Any]:
        """Clones or copies a plugin from a Git URL or local directory, then loads it."""
        name, version = await asyncio.to_thread(self._install_plugin_sync, source)
        loaded = await self.load_plugin(name)
        self._invalidate_web_caches()
        return {"status": "installed", "name": name, "version": version, "loaded": loaded}
