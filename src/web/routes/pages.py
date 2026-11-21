#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web HTML page routes."""

import logging

import aiohttp_jinja2
from aiohttp import web

from src.web.routes.api_plugins import PluginApiRoutes
from src.web.routes.api_tasks import TaskApiRoutes


log = logging.getLogger(__name__)


class PageRoutes:
    """Renders Jinja2 HTML templates for frontend views."""

    def __init__(self, server):
        self.server = server

    def _initial_data(self, collect):
        """Builds the snapshot embedded into the page.

        Rendering the list from HTML means the first paint already contains the
        data, instead of waiting for a JS fetch (or for a previous tab to have
        populated sessionStorage).
        """
        try:
            return collect()
        except Exception:
            log.exception("Failed to build initial page data")
            return None

    @aiohttp_jinja2.template("index.html")
    async def index(self, request):
        return {"title_key": "web.dashboard.title", "active": "dashboard"}

    @aiohttp_jinja2.template("tasks.html")
    async def tasks(self, request):
        tasks = self._initial_data(lambda: TaskApiRoutes(self.server.bot).collect())
        return {
            "title_key": "web.tasks.title",
            "active": "tasks",
            "initial_data": {"tasks": tasks or []},
        }

    @aiohttp_jinja2.template("plugins.html")
    async def plugins(self, request):
        plugins = self._initial_data(lambda: PluginApiRoutes(self.server.bot).collect())
        return {
            "title_key": "web.plugins.title",
            "active": "plugins",
            "initial_data": {"plugins": plugins or []},
        }

    @aiohttp_jinja2.template("logs.html")
    async def logs(self, request):
        return {"title_key": "web.logs.title", "active": "logs"}

    @aiohttp_jinja2.template("settings.html")
    async def settings(self, request):
        return {"title_key": "web.settings.title", "active": "settings"}

    def register(self, app: web.Application):
        """Registers page route handlers."""
        app.router.add_get("/", self.index)
        app.router.add_get("/tasks", self.tasks)
        app.router.add_get("/plugins", self.plugins)
        app.router.add_get("/logs", self.logs)
        app.router.add_get("/settings", self.settings)
