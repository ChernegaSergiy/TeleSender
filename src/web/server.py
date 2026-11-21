#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web server application runner and router setup."""

import asyncio
import time
from pathlib import Path
from aiohttp import web
import aiohttp_jinja2
import jinja2

from src.config.config import config
from src.services.logger import log
from src.services.translator import lang
from src.web.page_cache import PAGE_CACHE_KEY, PageCache, cache_html_pages
from src.web.plugin_routes import PluginPageRoutes, PluginTemplateLoader
from src.web.routes.api_plugins import PluginApiRoutes
from src.web.routes.api_settings import SettingsApiRoutes
from src.web.routes.api_tasks import TaskApiRoutes
from src.web.routes.api_media import MediaApiRoutes
from src.web.routes.pages import PageRoutes
from src.web.routes.auth import AuthRoutes
from src.web import auth
from src.web.two_factor import two_factor_manager


QUIET_PATHS = {"/api/logs", "/favicon.ico"}
QUIET_GET_PATHS = {"/api/bot", "/api/stats", "/api/tasks"}


@web.middleware
async def auth_middleware(request, handler):
    if not config.web_auth_enabled:
        return await handler(request)
    
    path = request.path
    
    if path.startswith("/static/") or path.startswith("/assets/") or path == "/favicon.ico":
        return await handler(request)
    
    if path == "/login" or path.startswith("/auth/"):
        return await handler(request)
    
    blocked, blocked_until = auth.is_blocked(request)
    if blocked:
        if path.startswith("/api/"):
            return web.json_response({'error': 'blocked'}, status=429)
        return web.HTTPFound('/login')
    
    session_id = request.cookies.get('telesender_session')
    session = auth.get_web_session(session_id)
    request["web_session"] = session
    
    if session:
        return await handler(request)
    
    if path.startswith("/api/"):
        return web.json_response({'error': 'unauthorized'}, status=401)
    
    return web.HTTPFound('/login')


@web.middleware
async def client_hints_middleware(request, handler):
    """Ask Chromium for high-entropy Client Hints on the login page.

    The hints (real device model / Android version) are only sent over HTTPS,
    so this is a no-op for plain HTTP deployments but future-proof behind a
    TLS reverse proxy.
    """
    response = await handler(request)
    if request.path == "/login" and isinstance(response, web.StreamResponse):
        hints = "Sec-CH-UA-Platform-Version, Sec-CH-UA-Model, Sec-CH-UA-Mobile"
        response.headers.setdefault("Accept-CH", hints)
        response.headers.setdefault("Vary", hints)
    return response


@web.middleware
async def log_web_request(request, handler):
    path = request.path
    if (
        path.startswith("/static/")
        or path.startswith("/ws/")
        or path in QUIET_PATHS
        or (request.method == "GET" and path in QUIET_GET_PATHS)
    ):
        return await handler(request)

    started = time.monotonic()
    status = 500
    try:
        response = await handler(request)
        status = response.status
        return response
    except web.HTTPException as exc:
        status = exc.status
        raise
    except Exception:
        log.exception(f"Web request failed: {request.method} {path}")
        raise
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        remote = request.remote or "-"
        message = f"Web {request.method} {path} -> {status} ({elapsed_ms:.1f} ms) from {remote}"
        if status >= 400:
            log.warning(message)
        else:
            log.info(message)


class Server:
    """Manages aiohttp HTTP server and routes for web dashboard."""

    def __init__(self, bot=None):
        self.bot = bot
        self.two_factor_manager = two_factor_manager
        self.start_time = time.time()
        # Allow large media uploads from the task form (default 200 MB).
        self.app = web.Application(
            client_max_size=max(1, config.web_max_upload_mb) * 1024 * 1024
        )
        self.page_cache = PageCache(
            ttl=config.web_page_cache_ttl,
            max_entries=config.web_page_cache_size,
            max_bytes=config.web_page_cache_bytes,
        )
        self.app[PAGE_CACHE_KEY] = self.page_cache
        self.app.middlewares.append(auth_middleware)
        self.app.middlewares.append(log_web_request)
        self.app.middlewares.append(cache_html_pages)
        self.app.middlewares.append(client_hints_middleware)
        self.plugin_page_routes = PluginPageRoutes(self)
        self._runner = None
        self._site = None

        self._setup_templates()
        self._setup_static()
        self._setup_routes()

    def invalidate_caches(self) -> None:
        """Drops rendered pages and navigation entries."""
        self.page_cache.clear()
        self.plugin_page_routes.invalidate_menu_cache()

    @property
    def running(self) -> bool:
        """Whether the web server is currently accepting connections."""
        return self._site is not None

    @staticmethod
    def _translate(key, **kwargs):
        value = lang.get(key, key)
        if not isinstance(value, str):
            return value
        for name, replacement in kwargs.items():
            value = value.replace("{" + name + "}", str(replacement))
        return value

    async def _inject_web_i18n(self, request):
        # APIs, static files and websockets never render Jinja templates, so
        # don't pay for translations/menu collection on every poll.
        if request.path.startswith(
            ("/api/", "/static/", "/assets/", "/ws/", "/favicon")
        ):
            return {"config": config, "request": request}
        return {
            "web_i18n": lang.get("web", {}),
            "current_lang": lang.get_lang(),
            "current_path": request.path,
            "plugin_menu_items": await self.plugin_page_routes.get_pinned_menu_items_async(),
            "config": config,
            "request": request,
        }

    def _setup_templates(self):
        templates_path = Path(__file__).parent / "templates"
        if not templates_path.exists():
            templates_path = Path("web") / "templates"
        env = aiohttp_jinja2.setup(
            self.app,
            loader=jinja2.ChoiceLoader([
                jinja2.FileSystemLoader(str(templates_path)),
                PluginTemplateLoader(self.bot),
            ]),
            context_processors=[self._inject_web_i18n],
        )
        env.globals["t"] = self._translate

    def _setup_static(self):
        static_path = Path(__file__).parent / "static"
        if not static_path.exists():
            static_path = Path("web") / "static"
        self.app.router.add_static("/static/", path=str(static_path), name="static")

        assets_path = Path(__file__).resolve().parents[2] / "assets"
        if not assets_path.exists():
            assets_path = Path("assets")
        if assets_path.exists():
            self.app.router.add_static("/assets/", path=str(assets_path), name="assets")

    def _setup_routes(self):
        AuthRoutes(self).register(self.app)
        PageRoutes(self).register(self.app)
        self.plugin_page_routes.register(self.app)
        TaskApiRoutes(self.bot).register(self.app)
        MediaApiRoutes(self.bot).register(self.app)
        PluginApiRoutes(self.bot).register(self.app)
        SettingsApiRoutes(self.bot, self.start_time).register(self.app)

        # Expose the bot on the app so plugin API dispatch routes can find it.
        self.app["_bot"] = self.bot

        # Let loaded plugins register their own web API routes.
        if self.bot:
            plugin_service = getattr(self.bot, "plugin_service", None)
            if plugin_service:
                for plugin_name in plugin_service.get_loaded_plugins():
                    plugin = plugin_service.get_plugin_instance(plugin_name)
                    if plugin and hasattr(plugin, "register_web_api_routes"):
                        try:
                            plugin.register_web_api_routes(self.app)
                        except Exception:
                            log.warning(
                                "Failed to register web API routes for plugin %s",
                                plugin_name,
                            )

    async def start(self):
        """Starts web server on configured host and port."""
        if self.running:
            return

        from src.web.ws import LogBroadcaster, ensure_websocket_log_handler

        ensure_websocket_log_handler()
        LogBroadcaster().set_loop(asyncio.get_running_loop())
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, config.web_host, config.web_port)
        try:
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site
        log.info(f"Web interface running on http://{config.web_host}:{config.web_port}")

    async def stop(self):
        """Stops the web server and releases the listening port."""
        if self._site is None and self._runner is None:
            return

        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        log.info("Web interface stopped")


class WebServerManager:
    """Controls the web dashboard lifecycle at runtime."""

    def __init__(self, bot=None):
        self.bot = bot
        self.server = None

    @property
    def running(self) -> bool:
        """Whether the web dashboard is currently running."""
        return bool(self.server and self.server.running)

    async def start(self) -> bool:
        """Starts the web dashboard. Returns False if it is already running."""
        if self.running:
            return False

        server = Server(self.bot)
        await server.start()
        self.server = server
        config.web_enabled = True
        return True

    async def stop(self) -> bool:
        """Stops the web dashboard. Returns False if it was not running."""
        if not self.running:
            self.server = None
            config.web_enabled = False
            return False

        await self.server.stop()
        self.server = None
        config.web_enabled = False
        return True


async def start_web_server(bot):
    """Entry point helper to launch web server in background task."""
    server = Server(bot)
    await server.start()
    return server
