#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web API routes for plugin management."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from aiohttp import web

from src.config.config import config
from src.services.plugin_scaffold import create_plugin_scaffold
from src.services.translator import lang
from src.web.plugin_routes import get_pages_for_plugin, invalidate_web_caches


PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PluginApiRoutes:
    """Handles REST API calls for plugin lifecycle."""

    def __init__(self, bot):
        self.bot = bot

    def _ensure_plugin_service(self):
        service = getattr(self.bot, "plugin_service", None)
        if not service:
            raise web.HTTPInternalServerError(text=json.dumps({"error": lang.get("web.errors.plugin_service_unavailable", "Plugin service not available")}), content_type="application/json")
        return service

    def _handler_counts(self, service, name):
        counts = {"commands": 0, "watchers": 0, "loops": 0, "callbacks": 0, "inline": 0}
        registry = getattr(service, "registry", None)
        if not registry:
            return counts

        mapping = {
            "command": "commands",
            "watcher": "watchers",
            "loop": "loops",
            "callback": "callbacks",
            "inline": "inline",
        }
        for handler in registry.get_plugin_handlers(name):
            key = mapping.get(handler.handler_type)
            if key:
                counts[key] += 1
        return counts

    def collect(self):
        """Serializes every plugin. Shared by the JSON API and the initial page render."""
        service = self._ensure_plugin_service()
        loaded = service.get_loaded_plugins()
        all_plugins = service.find_plugins()

        result = []
        for name in all_plugins:
            manifest_path = os.path.join("plugins", name, "manifest.json")
            manifest = {}
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                except Exception:
                    pass

            is_loaded = name in loaded
            result.append({
                "name": name,
                "id": manifest.get("id", "—"),
                "version": manifest.get("version", "?"),
                "author": manifest.get("author", "?"),
                "description": manifest.get("description", ""),
                "category": manifest.get("category", "General"),
                "loaded": is_loaded,
                "handlers": self._handler_counts(service, name),
                "pages": get_pages_for_plugin(self.bot, name),
            })

        result.sort(key=lambda x: x["name"])
        return result

    async def get_all(self, request):
        return web.json_response({"plugins": self.collect()})

    async def get_one(self, request):
        service = self._ensure_plugin_service()
        name = request.match_info["name"]
        manifest_path = os.path.join("plugins", name, "manifest.json")

        if not os.path.exists(manifest_path):
            return web.json_response({"error": lang.get("web.errors.plugin_not_found", "Plugin not found")}, status=404)

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_manifest", "Invalid manifest")}, status=500)

        loaded = service.get_loaded_plugins()
        return web.json_response({
            "name": name,
            "id": manifest.get("id", "—"),
            "version": manifest.get("version", "?"),
            "author": manifest.get("author", "?"),
            "description": manifest.get("description", ""),
            "category": manifest.get("category", "General"),
            "loaded": name in loaded,
            "handlers": self._handler_counts(service, name),
            "pages": get_pages_for_plugin(self.bot, name),
        })

    async def pin_page(self, request):
        service = self._ensure_plugin_service()
        db = getattr(self.bot, "db", None) if self.bot else None
        if not db or not hasattr(db, "set_plugin_page_pinned"):
            return web.json_response(
                {"error": lang.get("web.errors.plugin_page_pin_failed", "Failed to update plugin page pin")},
                status=500,
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_json", "Invalid JSON")}, status=400)

        plugin_name = str(data.get("plugin") or "").strip()
        page_path = str(data.get("path") or "").strip()
        pinned = bool(data.get("pinned", True))
        if not plugin_name or not page_path:
            return web.json_response(
                {"error": lang.get("web.errors.plugin_page_not_found", "Plugin page not found")},
                status=404,
            )

        if pinned:
            registry = getattr(service, "pages", None)
            page_definition = registry.get(plugin_name, page_path) if registry else None
            if plugin_name not in service.get_loaded_plugins() or not page_definition:
                return web.json_response(
                    {"error": lang.get("web.errors.plugin_page_not_found", "Plugin page not found")},
                    status=404,
                )

        db.set_plugin_page_pinned(plugin_name, page_path, pinned)
        invalidate_web_caches(self.bot)
        return web.json_response({
            "success": True,
            "plugin": plugin_name,
            "path": page_path,
            "pinned": pinned,
        })

    async def load(self, request):
        service = self._ensure_plugin_service()
        name = request.match_info["name"]
        if name in service.get_loaded_plugins():
            return web.json_response({"error": lang.get("web.errors.already_loaded", "Already loaded")}, status=400)

        ok = await service.load_plugin(name)
        if ok:
            invalidate_web_caches(self.bot)
        return web.json_response({"success": ok})

    async def unload(self, request):
        service = self._ensure_plugin_service()
        name = request.match_info["name"]
        if name not in service.get_loaded_plugins():
            return web.json_response({"error": lang.get("web.errors.not_loaded", "Not loaded")}, status=400)

        ok = await service.unload_plugin(name)
        if ok:
            invalidate_web_caches(self.bot)
        return web.json_response({"success": ok})

    async def reload(self, request):
        service = self._ensure_plugin_service()
        name = request.match_info["name"]
        ok = await service.reload_plugin(name)
        if ok:
            invalidate_web_caches(self.bot)
        return web.json_response({"success": ok})

    async def update(self, request):
        service = self._ensure_plugin_service()
        name = request.match_info["name"]

        try:
            data = await request.json()
        except Exception:
            data = {}

        force = bool(data.get("force", False))

        try:
            result = await service.update_plugin(name, force=force)
        except Exception as e:
            return web.json_response({"error": f"{lang.get('web.errors.update_failed', 'Failed to update plugin')}: {e}"}, status=500)

        status = result.get("status")
        invalidate_web_caches(self.bot)

        if status == "updated":
            return web.json_response({
                "success": True,
                "message": lang.get("web.plugins.updated_message", "Plugin {name} updated: v{old} -> v{new}").format(name=name, old=result["old_version"], new=result["new_version"]),
                "old_version": result["old_version"],
                "new_version": result["new_version"],
            })

        if status == "no_changes":
            return web.json_response({
                "success": False,
                "no_changes": True,
                "message": lang.get("web.plugins.up_to_date_message", "Plugin {name} is already up to date (v{version})").format(name=name, version=result["version"]),
                "version": result["version"],
            })

        if status == "newer_installed":
            return web.json_response({
                "success": False,
                "newer_installed": True,
                "message": lang.get("web.plugins.newer_installed_message", "Newer version installed: v{old} > v{new}").format(old=result["old_version"], new=result["new_version"]),
                "old_version": result["old_version"],
                "new_version": result["new_version"],
            })

        return web.json_response({"error": result.get("error", lang.get("web.errors.update_failed", "Update failed"))}, status=400)

    @staticmethod
    def _plugin_error(key: str, fallback: str, status: int = 400, **kwargs):
        message = lang.get(key, fallback).format(**kwargs)
        return web.json_response({"error": message}, status=status)

    async def create(self, request):
        service = self._ensure_plugin_service()

        try:
            data = await request.json()
        except Exception:
            return self._plugin_error("web.errors.invalid_json", "Invalid JSON")

        name = str(data.get("name") or "").strip()
        if not name:
            return self._plugin_error("web.plugins.error_name", "Enter name")
        if not PLUGIN_NAME_RE.fullmatch(name):
            return self._plugin_error(
                "web.errors.invalid_plugin_name",
                "Invalid plugin name. Use only letters, digits, _ and -",
            )

        author = str(data.get("author") or "").strip() or lang.get(
            "messages.plugins_create_default_author", "User"
        )
        description = str(data.get("description") or "").strip() or lang.get(
            "messages.plugins_create_default_description", "My plugin"
        )
        category = str(data.get("category") or "").strip() or lang.get(
            "messages.plugins_create_default_category", "General"
        )
        create_web_page = bool(data.get("create_web_page"))

        try:
            created = create_plugin_scaffold(
                name=name,
                author=author,
                description=description,
                category=category,
                create_web_page=create_web_page,
                plugins_dir=config.plugins_dir,
            )
        except FileExistsError:
            return self._plugin_error(
                "web.errors.plugin_exists",
                "Plugin '{name}' already exists",
                status=409,
                name=name,
            )
        except ValueError as exc:
            return self._plugin_error(
                "web.errors.create_failed",
                "Failed to create plugin: {error}",
                status=400,
                error=exc,
            )
        except Exception as exc:
            return self._plugin_error(
                "web.errors.create_failed",
                "Failed to create plugin: {error}",
                status=500,
                error=exc,
            )

        loaded = await service.load_plugin(name)
        invalidate_web_caches(self.bot)
        return web.json_response({
            "success": True,
            "name": created["name"],
            "id": created["id"],
            "path": created["name"],
            "loaded": loaded,
        })

    def _install_plugin_sync(self, source: str):
        source = source.strip()
        if not source:
            raise ValueError(lang.get("messages.plugins_invalid_source", "Invalid path or URL"))

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

                target_dir = os.path.join(config.plugins_dir, name)
                if os.path.exists(target_dir):
                    raise FileExistsError(name)

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
            target_dir = os.path.join(config.plugins_dir, name)
            if os.path.exists(target_dir):
                raise FileExistsError(name)
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

    async def install(self, request):
        service = self._ensure_plugin_service()

        try:
            data = await request.json()
        except Exception:
            return self._plugin_error("web.errors.invalid_json", "Invalid JSON")

        source = str(data.get("url") or data.get("source") or "").strip()
        if not source:
            return self._plugin_error(
                "web.errors.invalid_plugin_source",
                "Invalid path or URL",
            )

        try:
            name, version = await asyncio.to_thread(self._install_plugin_sync, source)
        except FileExistsError as e:
            existing = str(e.args[0]) if e.args else source
            return self._plugin_error(
                "web.errors.plugin_exists",
                "Plugin '{name}' already exists",
                status=409,
                name=existing,
            )
        except Exception as e:
            return self._plugin_error(
                "web.errors.install_failed",
                "Failed to install plugin: {error}",
                status=500,
                error=e,
            )

        loaded = await service.load_plugin(name)
        invalidate_web_caches(self.bot)
        return web.json_response({
            "success": True,
            "name": name,
            "version": version,
            "loaded": loaded,
        })

    async def delete(self, request):
        service = self._ensure_plugin_service()
        name = request.match_info["name"]
        if not PLUGIN_NAME_RE.fullmatch(name):
            return self._plugin_error(
                "web.errors.invalid_plugin_name",
                "Invalid plugin name. Use only letters, digits, _ and -",
            )

        plugin_dir = os.path.join(config.plugins_dir, name)
        if not os.path.isdir(plugin_dir):
            return web.json_response(
                {"error": lang.get("web.errors.plugin_not_found", "Plugin not found")},
                status=404,
            )

        if name in service.get_loaded_plugins():
            await service.unload_plugin(name)

        try:
            shutil.rmtree(plugin_dir)
        except Exception as e:
            return self._plugin_error(
                "web.errors.delete_failed",
                "Failed to delete plugin: {error}",
                status=500,
                error=e,
            )

        invalidate_web_caches(self.bot)
        return web.json_response({"success": True, "name": name})

    def register(self, app: web.Application):
        """Registers plugin API routes."""
        app.router.add_get("/api/plugins", self.get_all)
        app.router.add_get("/api/plugins/{name}", self.get_one)
        app.router.add_post("/api/plugin-pages/pin", self.pin_page)
        app.router.add_post("/api/plugins/create", self.create)
        app.router.add_post("/api/plugins/install", self.install)
        app.router.add_delete("/api/plugins/{name}", self.delete)
        app.router.add_post("/api/plugins/{name}/load", self.load)
        app.router.add_post("/api/plugins/{name}/unload", self.unload)
        app.router.add_post("/api/plugins/{name}/reload", self.reload)
        app.router.add_post("/api/plugins/{name}/update", self.update)
