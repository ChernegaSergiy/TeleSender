#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Routes, template loading, and navigation for plugin-provided web pages."""

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

import aiohttp_jinja2
import jinja2
from aiohttp import web

from core.pages import normalize_page_path
from src.config.config import config
from src.services.translator import lang
from src.web.page_cache import TTL_HEADER

PLUGIN_TEMPLATE_PREFIX = "plugin:"
PLUGIN_ROUTE_PREFIX = "/plugin/"

# Pinned navigation entries rarely change; cache them so every page render does
# not have to run every plugin's sync ``get_menu_items`` hook.
MENU_CACHE_TTL = 5.0


def _plugin_service(bot):
    """Returns the plugin service from a bot instance."""
    return getattr(bot, "plugin_service", None) if bot else None


def _loaded_instance(service, plugin_name: str):
    """Returns a plugin instance only when the plugin is loaded."""
    if not service or plugin_name not in service.get_loaded_plugins():
        return None
    return service.get_plugin_instance(plugin_name)


def invalidate_web_caches(bot) -> None:
    """Clears cached pages and navigation entries after content changes."""
    manager = getattr(bot, "web_server", None) if bot else None
    server = getattr(manager, "server", None) if manager else None
    if not server:
        return
    invalidate = getattr(server, "invalidate_caches", None)
    if callable(invalidate):
        invalidate()
        return
    routes = getattr(server, "plugin_page_routes", None)
    if routes:
        routes.invalidate_menu_cache()


def get_pinned_page_keys(bot) -> List[Tuple[str, str]]:
    """Returns pinned plugin pages in menu order."""
    db = getattr(bot, "db", None) if bot else None
    get_pinned = getattr(db, "get_pinned_plugin_pages", None) if db else None
    if not callable(get_pinned):
        return []

    try:
        rows = get_pinned()
    except Exception:
        logging.getLogger(__name__).exception("Failed to load pinned plugin pages")
        return []

    keys = []
    seen = set()
    for row in rows:
        if isinstance(row, dict):
            plugin_name = row.get("plugin_name")
            page_path = row.get("page_path")
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            plugin_name, page_path = row[0], row[1]
        else:
            continue

        key = (str(plugin_name or ""), str(page_path or ""))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def resolve_page_title(plugin, page_definition) -> str:
    """Resolves a page title using the plugin's localization when available."""
    if page_definition.title_key:
        title = plugin.t(page_definition.title_key)
        if title and title != page_definition.title_key:
            return str(title)
    return str(page_definition.title or page_definition.path)


def page_url(plugin_name: str, path: str) -> str:
    """Builds the web URL for a plugin page."""
    return (
        f"{PLUGIN_ROUTE_PREFIX}{quote(str(plugin_name), safe='')}/"
        f"{quote(str(path), safe='/')}"
    )


def static_url(plugin_name: str) -> str:
    """Builds the static asset base URL for a plugin."""
    return f"{PLUGIN_ROUTE_PREFIX}{quote(str(plugin_name), safe='')}/static"


_IMAGE_EXTENSIONS = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".avif")


def normalize_icon(plugin_name: str, icon: Any) -> Optional[str]:
    """Normalizes a plugin navigation icon.

    Supported forms:
    - ``None`` / empty string — the default pin icon is used.
    - Lucide icon name, e.g. ``"sparkles"``.
    - Emoji or short text, e.g. ``"🚀"``.
    - Absolute URL/path, e.g. ``"/assets/logo.svg"``.
    - Image file inside the plugin's ``static/`` folder, e.g. ``"icon.svg"``.
    - Raw inline SVG starting with ``<svg``.
    """
    if icon is None:
        return None

    value = str(icon).strip()
    if not value:
        return None

    lowered = value.lower()
    if lowered.startswith("<svg"):
        return value
    if lowered.startswith(("/", "http://", "https://", "data:image/")):
        return value

    path_only = lowered.split("?", 1)[0].split("#", 1)[0]
    if lowered.startswith("static/"):
        relative = value[len("static/"):]
    elif path_only.endswith(_IMAGE_EXTENSIONS):
        relative = value
    else:
        # Lucide icon name or emoji — rendered as-is.
        return value

    if relative.startswith("./"):
        relative = relative[2:]
    if not relative or "\\" in relative:
        return None
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None

    return f"{static_url(plugin_name)}/{relative}"


def serialize_page(
    plugin_name: str,
    plugin,
    page_definition,
    pinned: bool = False,
    icon: Optional[str] = None,
) -> Dict:
    """Converts a plugin page into API metadata."""
    return {
        "path": page_definition.path,
        "title": resolve_page_title(plugin, page_definition),
        "url": page_url(plugin_name, page_definition.path),
        "template": page_definition.template,
        "plugin": plugin_name,
        "plugin_title": plugin.name,
        "icon": normalize_icon(
            plugin_name,
            icon
            if icon is not None
            else (
                getattr(page_definition, "icon", None)
                or getattr(plugin, "_manifest", {}).get("icon")
            ),
        ),
        "pinned": bool(pinned),
    }


def _menu_icon_map(plugin_name: str, plugin) -> Dict[str, str]:
    """Maps plugin menu URLs to their icons for the pages API."""
    try:
        items = plugin.get_menu_items()
    except Exception:
        logging.getLogger(__name__).exception(
            "Plugin menu hook failed while reading icons: %s", plugin_name
        )
        return {}
    if not isinstance(items, (list, tuple)):
        return {}

    icons: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        icon = normalize_icon(plugin_name, item.get("icon"))
        url = str(item.get("url") or "").strip()
        if not icon or not url:
            continue
        if not url.startswith("/"):
            try:
                url = page_url(plugin_name, normalize_page_path(url))
            except ValueError:
                continue
        icons[urlsplit(url).path] = icon
    return icons


def get_pages_for_plugin(bot, plugin_name: str) -> List[Dict]:
    """Returns serialized pages for one loaded plugin."""
    service = _plugin_service(bot)
    plugin = _loaded_instance(service, plugin_name)
    registry = getattr(service, "pages", None) if service else None
    if not plugin or not registry:
        return []

    pinned_keys = set(get_pinned_page_keys(bot))
    menu_icons = _menu_icon_map(plugin_name, plugin)
    pages = [
        serialize_page(
            plugin_name,
            plugin,
            page_definition,
            pinned=(plugin_name, page_definition.path) in pinned_keys,
            icon=menu_icons.get(page_url(plugin_name, page_definition.path)),
        )
        for page_definition in registry.list_for_plugin(plugin_name)
    ]
    pages.sort(key=lambda item: item["path"])
    return pages


def find_plugin_page(bot, plugin_name: str, path: str) -> Tuple[Optional[object], Optional[object]]:
    """Finds a page definition and its owning loaded plugin instance."""
    service = _plugin_service(bot)
    plugin = _loaded_instance(service, plugin_name)
    registry = getattr(service, "pages", None) if service else None
    if not plugin or not registry:
        return None, None
    return plugin, registry.get(plugin_name, path)


class PluginTemplateLoader(jinja2.BaseLoader):
    """Loads templates from plugin-specific templates folders."""

    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("PluginTemplateLoader")

    def get_source(self, environment, template):
        if not template.startswith(PLUGIN_TEMPLATE_PREFIX):
            raise jinja2.TemplateNotFound(template)

        plugin_name, separator, relative = template[len(PLUGIN_TEMPLATE_PREFIX):].partition("/")
        if not separator or not plugin_name or not relative:
            raise jinja2.TemplateNotFound(template)

        service = _plugin_service(self.bot)
        plugin = _loaded_instance(service, plugin_name)
        plugin_path = getattr(plugin, "path", None) if plugin else None
        if not plugin_path:
            raise jinja2.TemplateNotFound(template)

        base = (Path(plugin_path) / "templates").resolve()
        target = (base / relative).resolve()
        if target != base and base not in target.parents:
            raise jinja2.TemplateNotFound(template)
        if not target.is_file():
            raise jinja2.TemplateNotFound(template)

        source = target.read_text(encoding="utf-8")
        mtime = target.stat().st_mtime

        def uptodate():
            try:
                return target.stat().st_mtime == mtime
            except OSError:
                return False

        return source, str(target), uptodate


class PluginPageRoutes:
    """Serves plugin pages, static assets, and navigation entries."""

    def __init__(self, server):
        self.server = server
        self.bot = server.bot
        self.logger = logging.getLogger("PluginPageRoutes")
        self._menu_cache: Optional[List[Dict]] = None
        self._menu_cache_time = 0.0
        self._menu_cache_lock = asyncio.Lock()

    def invalidate_menu_cache(self) -> None:
        """Forces the next render to recollect plugin navigation entries."""
        self._menu_cache = None
        self._menu_cache_time = 0.0

    async def _call(self, handler, *args):
        """Calls a sync or async plugin hook.

        Async hooks run on the event loop as usual. Sync hooks are moved to a
        worker thread so a slow plugin (network, disk, ...) cannot block every
        other web request and the bot itself. Plugin instances are shared with
        the bot, so sync hooks should not mutate shared state in ways that
        assume single-threaded execution.
        """
        if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
            getattr(handler, "__func__", None)
        ):
            return await handler(*args)

        result = await asyncio.to_thread(handler, *args)
        if inspect.isawaitable(result):
            result = await result
        return result

    def get_menu_items(self) -> List[Dict]:
        """Collects navigation entries from all loaded plugins."""
        service = _plugin_service(self.bot)
        if not service:
            return []

        menu_items = []
        for plugin_name in service.get_loaded_plugins():
            plugin = service.get_plugin_instance(plugin_name)
            if not plugin:
                continue

            try:
                items = plugin.get_menu_items()
            except Exception:
                self.logger.exception("Plugin menu hook failed: %s", plugin_name)
                continue

            if not isinstance(items, (list, tuple)):
                self.logger.warning("Plugin menu hook must return a list: %s", plugin_name)
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue

                name = str(item.get("name") or "").strip()
                url = str(item.get("url") or "").strip()
                if not name or not url:
                    continue

                if "://" in url or url.startswith(("//", "javascript:", "data:")):
                    self.logger.warning("Skipping unsafe plugin menu URL: %r", url)
                    continue
                if not url.startswith("/"):
                    try:
                        url = page_url(plugin_name, normalize_page_path(url))
                    except ValueError:
                        self.logger.warning("Skipping invalid plugin menu URL: %r", url)
                        continue

                parsed_url = urlsplit(url)
                path_segments = unquote(parsed_url.path).split("/")
                if parsed_url.scheme or parsed_url.netloc or any(
                    segment in (".", "..") for segment in path_segments
                ):
                    self.logger.warning("Skipping unsafe plugin menu URL: %r", url)
                    continue

                menu_items.append({
                    "name": name,
                    "url": url,
                    "path": parsed_url.path,
                    "icon": normalize_icon(plugin_name, item.get("icon")),
                    "plugin": plugin_name,
                    "plugin_title": plugin.name,
                })

        return menu_items

    def get_pinned_menu_items(self) -> List[Dict]:
        """Returns pinned plugin pages for the bottom navigation."""
        service = _plugin_service(self.bot)
        pinned = get_pinned_page_keys(self.bot)
        if not service or not pinned:
            return []

        menu_by_page = {
            (item["plugin"], item["path"]): item
            for item in self.get_menu_items()
        }

        items = []
        for plugin_name, page_path in pinned:
            plugin = _loaded_instance(service, plugin_name)
            registry = getattr(service, "pages", None)
            if not plugin or not registry:
                continue

            page_definition = registry.get(plugin_name, page_path)
            if not page_definition:
                continue

            url = page_url(plugin_name, page_path)
            path = urlsplit(url).path
            menu_item = menu_by_page.get((plugin_name, path), {})
            icon = (
                menu_item.get("icon")
                or getattr(page_definition, "icon", None)
                or getattr(plugin, "_manifest", {}).get("icon")
            )
            items.append({
                "name": resolve_page_title(plugin, page_definition),
                "url": url,
                "path": path,
                "icon": normalize_icon(plugin_name, icon),
                "active_key": f"plugin:{plugin_name}:{page_path}",
                "plugin": plugin_name,
                "plugin_title": plugin.name,
            })
        return items

    async def get_menu_items_async(self) -> List[Dict]:
        """Collects navigation entries without blocking the event loop."""
        return await asyncio.to_thread(self.get_menu_items)

    async def get_pinned_menu_items_async(self) -> List[Dict]:
        """Returns pinned menu items, cached for a few seconds."""
        now = time.monotonic()
        cached = self._menu_cache
        if cached is not None and now - self._menu_cache_time < MENU_CACHE_TTL:
            return cached

        async with self._menu_cache_lock:
            now = time.monotonic()
            if (
                self._menu_cache is not None
                and now - self._menu_cache_time < MENU_CACHE_TTL
            ):
                return self._menu_cache

            items = await asyncio.to_thread(self.get_pinned_menu_items)
            self._menu_cache = items
            self._menu_cache_time = now
            return items

    @staticmethod
    def _apply_page_cache_ttl(response: web.StreamResponse, page_definition) -> web.StreamResponse:
        """Communicates the per-page cache policy to the HTML cache middleware."""
        ttl = getattr(page_definition, "cache_ttl", None)
        if ttl is None or not isinstance(response, web.StreamResponse):
            return response
        if ttl <= 0:
            response.headers.pop(TTL_HEADER, None)
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers[TTL_HEADER] = str(ttl)
        return response

    async def page(self, request):
        """Renders a page provided by a loaded plugin."""
        service = _plugin_service(self.bot)
        if not service:
            raise web.HTTPInternalServerError(
                text=lang.get("web.errors.plugin_service_unavailable", "Plugin service not available")
            )

        plugin_name = request.match_info["plugin_name"]
        page_path = request.match_info["page_path"]
        plugin, page_definition = find_plugin_page(self.bot, plugin_name, page_path)
        if not plugin or not page_definition:
            raise web.HTTPNotFound(
                text=lang.get("web.errors.plugin_page_not_found", "Plugin page not found")
            )

        context = dict(page_definition.context)

        if page_definition.handler:
            try:
                result = await self._call(page_definition.handler, request)
            except web.HTTPException:
                raise
            except Exception:
                self.logger.exception(
                    "Plugin page handler failed: %s/%s", plugin_name, page_path
                )
                raise web.HTTPInternalServerError(
                    text=lang.get("web.errors.plugin_page_failed", "Failed to render plugin page")
                )

            if isinstance(result, web.StreamResponse):
                return self._apply_page_cache_ttl(result, page_definition)
            if isinstance(result, dict):
                context.update(result)
            elif result is not None:
                context["content"] = str(result)

        try:
            render_context = await self._call(
                plugin.on_page_render, page_path, request
            )
        except web.HTTPException:
            raise
        except Exception:
            self.logger.exception(
                "Plugin page render hook failed: %s/%s", plugin_name, page_path
            )
            raise web.HTTPInternalServerError(
                text=lang.get("web.errors.plugin_page_failed", "Failed to render plugin page")
            )

        if isinstance(render_context, dict):
            context.update(render_context)
        elif render_context is not None:
            self.logger.warning(
                "Plugin on_page_render must return a dict: %s/%s", plugin_name, page_path
            )

        title = resolve_page_title(plugin, page_definition)
        context.update({
            "plugin_instance": plugin,
            "plugin": plugin,
            "request": request,
            "config": config,
            "t": self.server._translate,
            "plugin_t": plugin.t,
            "page": page_definition,
            "page_path": page_path,
            "plugin_name": plugin_name,
            "plugin_title": plugin.name,
            "plugin_static_url": static_url(plugin_name),
            "current_path": request.path,
        })
        context.setdefault("page_title", title)
        context.setdefault("title_key", title)
        context.setdefault("active", f"plugin:{plugin_name}:{page_path}")

        template_name = f"{PLUGIN_TEMPLATE_PREFIX}{plugin_name}/{page_definition.template}"
        try:
            response = aiohttp_jinja2.render_template(template_name, request, context)
        except jinja2.TemplateError:
            self.logger.exception("Plugin page template failed: %s", template_name)
            raise web.HTTPInternalServerError(
                text=lang.get("web.errors.plugin_page_failed", "Failed to render plugin page")
            )
        return self._apply_page_cache_ttl(response, page_definition)

    async def static(self, request):
        """Serves files from a loaded plugin's static folder."""
        plugin_name = request.match_info["plugin_name"]
        relative = request.match_info["file_path"]
        service = _plugin_service(self.bot)
        plugin = _loaded_instance(service, plugin_name)
        plugin_path = getattr(plugin, "path", None) if plugin else None
        if not plugin_path or not relative:
            raise web.HTTPNotFound()

        base = (Path(plugin_path) / "static").resolve()
        target = (base / relative).resolve()
        if target != base and base not in target.parents:
            raise web.HTTPNotFound()
        if not target.is_file():
            raise web.HTTPNotFound()

        return web.FileResponse(target)

    def register(self, app: web.Application):
        """Registers plugin page and static routes."""
        app.router.add_get(
            "/plugin/{plugin_name}/static/{file_path:.*}", self.static
        )
        app.router.add_get("/plugin/{plugin_name}/{page_path:.*}", self.page)
