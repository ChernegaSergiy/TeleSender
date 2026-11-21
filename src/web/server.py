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
PUBLIC_AUTH_PATHS = {
    "/login",
    "/auth/login",
    "/auth/bot-login",
    "/auth/telegram-login",
    "/auth/telegram-webapp",
    "/auth/2fa/status",
    "/auth/2fa/cancel",
    "/auth/logout",
}


def _is_public_web_path(path: str) -> bool:
    return (
        path in PUBLIC_AUTH_PATHS
        or path.startswith("/static/")
        or path.startswith("/assets/")
        or path == "/favicon.ico"
    )


class _GuardedPluginRouter:
    """Prevents plugins from registering routes that bypass authentication."""

    def __init__(self, router):
        self._router = router

    @staticmethod
    def _guard(path):
        if isinstance(path, str) and (_is_public_web_path(path) or path.startswith("/auth/")):
            raise ValueError(f"Plugin route is not allowed: {path}")
        return path

    def add_route(self, method, path, handler, **kwargs):
        self._guard(path)
        return self._router.add_route(method, path, handler, **kwargs)

    def add_get(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_get(path, *args, **kwargs)

    def add_post(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_post(path, *args, **kwargs)

    def add_put(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_put(path, *args, **kwargs)

    def add_delete(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_delete(path, *args, **kwargs)

    def add_patch(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_patch(path, *args, **kwargs)

    def add_head(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_head(path, *args, **kwargs)

    def add_options(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_options(path, *args, **kwargs)

    def add_view(self, path, *args, **kwargs):
        self._guard(path)
        return self._router.add_view(path, *args, **kwargs)

    def add_static(self, prefix, path, **kwargs):
        self._guard(prefix)
        return self._router.add_static(prefix, path, **kwargs)

    def __getattr__(self, name):
        return getattr(self._router, name)


class _GuardedPluginApp:
    """App proxy passed to plugins for route registration."""

    def __init__(self, app):
        self._app = app
        self.router = _GuardedPluginRouter(app.router)

    def __getitem__(self, key):
        return self._app[key]

    def __setitem__(self, key, value):
        self._app[key] = value

    def __getattr__(self, name):
        return getattr(self._app, name)


@web.middleware
async def auth_middleware(request, handler):
    if not config.web_auth_enabled:
        return await handler(request)

    path = request.path

    if _is_public_web_path(path):
        # Public auth endpoints handle their own checks/rate limits.
        return await handler(request)

    blocked, blocked_until = auth.is_blocked(request)
    if blocked:
        if path.startswith("/api/") or path.startswith("/ws/"):
            return web.json_response({'error': 'blocked'}, status=429)
        return web.HTTPFound('/login')

    session_id = request.cookies.get(auth.SESSION_COOKIE)
    session = auth.get_web_session(session_id)
    request["web_session"] = session

    if session:
        return await handler(request)

    if path.startswith("/api/") or path.startswith("/ws/"):
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


MAX_JSON_BODY_BYTES = 1024 * 1024


@web.middleware
async def csrf_middleware(request, handler):
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        content_length = request.content_length
        if request.path != "/api/media/upload":
            if content_length is not None and content_length > MAX_JSON_BODY_BYTES:
                return web.json_response({"error": "Request body too large"}, status=413)
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin:
            if not auth.is_trusted_origin(request):
                if request.path.startswith("/api/") or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
                    return web.json_response({"error": "invalid origin"}, status=403)
                return web.Response(text="Forbidden", status=403)
        else:
            has_session_cookie = bool(request.cookies.get(auth.SESSION_COOKIE) or request.cookies.get(auth.TWO_FACTOR_COOKIE))
            is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
            is_json = (request.content_type or "").lower() == "application/json"
            if has_session_cookie and not (is_ajax or is_json):
                if request.path.startswith("/api/"):
                    return web.json_response({"error": "missing origin"}, status=403)
                return web.Response(text="Forbidden", status=403)
    return await handler(request)


@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(f"Unhandled web error: {request.method} {request.path}")
        if request.path.startswith("/api/") or request.path.startswith("/ws/") \
                or request.headers.get("X-Requested-With") == "XMLHttpRequest" \
                or (request.content_type or "").lower() == "application/json":
            return web.json_response({"error": "Internal server error"}, status=500)
        return web.Response(text="Internal server error", status=500)


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
        self.app.middlewares.append(error_middleware)
        self.app.middlewares.append(auth_middleware)
        self.app.middlewares.append(csrf_middleware)
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
                guarded_app = _GuardedPluginApp(self.app)
                for plugin_name in plugin_service.get_loaded_plugins():
                    plugin = plugin_service.get_plugin_instance(plugin_name)
                    if plugin and hasattr(plugin, "register_web_api_routes"):
                        try:
                            plugin.register_web_api_routes(guarded_app)
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
        for warning in config.security_warnings():
            log.warning(f"Security warning: {warning}")

    async def stop(self):
        """Stops the web server and releases the listening port."""
        if self._site is None and self._runner is None:
            return

        try:
            from src.web.ws import LogBroadcaster

            await LogBroadcaster().close_all()
        except Exception:
            pass

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
