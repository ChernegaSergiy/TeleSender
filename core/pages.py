#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registration metadata and registry for plugin-provided web pages."""

import inspect
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

_RESERVED_PREFIXES = ("static/",)


def normalize_page_path(path: str) -> str:
    """Validates and normalizes a plugin page path."""
    normalized = str(path or "").strip().strip("/")
    if not normalized or "\\" in normalized:
        raise ValueError("Page path must be a non-empty URL path")

    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Invalid page path: {path!r}")

    if normalized == "static" or normalized.startswith(_RESERVED_PREFIXES):
        raise ValueError(f"Page path is reserved for plugin static files: {path!r}")

    return normalized


def normalize_template_path(template: str) -> str:
    """Validates a template path relative to the plugin's templates folder."""
    normalized = str(template or "").strip()
    if not normalized or "\\" in normalized:
        raise ValueError("Template path must be a non-empty relative path")

    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ValueError(f"Invalid template path: {template!r}")

    return str(path)


@dataclass
class PageDefinition:
    """A page exposed by a loaded plugin."""

    plugin_name: str
    path: str
    template: str
    handler: Optional[Callable] = None
    context: Dict[str, Any] = field(default_factory=dict)
    title: Optional[str] = None
    title_key: Optional[str] = None
    icon: Optional[str] = None
    cache_ttl: Optional[float] = None


def page(path: str, template: str, *, context: Optional[Mapping[str, Any]] = None,
         title: Optional[str] = None, title_key: Optional[str] = None,
         icon: Optional[str] = None, cache_ttl: Optional[float] = None) -> Callable:
    """
    Registers a plugin method as a web page.

    Args:
        path: URL path below /plugin/<plugin_name>/
        template: Template path below the plugin's templates/ folder
        context: Static values added to the page render context
        title: Default page title
        title_key: Plugin localization key used as the page title
        icon: Icon shown when the page is pinned to the bottom navigation.
            Accepts a Lucide icon name (``"sparkles"``), an emoji, a URL, or
            a path inside the plugin's ``static/`` folder (``"icon.svg"``).
        cache_ttl: Seconds to cache the rendered page. ``None`` uses the
            global web cache TTL, ``0`` disables caching for this page.

    Returns:
        Decorator
    """
    normalized_path = normalize_page_path(path)
    normalized_template = normalize_template_path(template)
    if context is not None and not isinstance(context, Mapping):
        raise TypeError("Page context must be a mapping")

    if cache_ttl is not None:
        try:
            cache_ttl = float(cache_ttl)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid page cache TTL: {cache_ttl!r}")
        if cache_ttl < 0:
            raise ValueError("Page cache TTL cannot be negative")

    static_context = dict(context or {})

    def decorator(func: Callable) -> Callable:
        func._is_plugin_page = True
        func._plugin_page_config = {
            "path": normalized_path,
            "template": normalized_template,
            "context": static_context,
            "title": title,
            "title_key": title_key,
            "icon": icon,
            "cache_ttl": cache_ttl,
        }
        return func

    return decorator


def collect_page_definitions(plugin_name: str, plugin_instance: Any) -> List[PageDefinition]:
    """Finds methods decorated with @page on a plugin instance."""
    pages = []
    for name, member in inspect.getmembers(type(plugin_instance), predicate=inspect.isfunction):
        config = getattr(member, "_plugin_page_config", None)
        if not isinstance(config, dict):
            continue

        handler = getattr(plugin_instance, name, None)
        if not callable(handler):
            continue

        pages.append(PageDefinition(plugin_name=plugin_name, handler=handler, **config))
    return pages


class PageRegistry:
    """In-memory registry of pages exposed by loaded plugins."""

    def __init__(self):
        self._pages: Dict[Tuple[str, str], PageDefinition] = {}

    def register(self, page_definition: PageDefinition) -> bool:
        """Registers or replaces a plugin page."""
        key = (page_definition.plugin_name, page_definition.path)
        self._pages[key] = page_definition
        return True

    def unregister_plugin(self, plugin_name: str) -> int:
        """Removes all pages belonging to a plugin."""
        keys = [key for key in self._pages if key[0] == plugin_name]
        for key in keys:
            del self._pages[key]
        return len(keys)

    def get(self, plugin_name: str, path: str) -> Optional[PageDefinition]:
        """Returns a page by plugin folder name and page path."""
        return self._pages.get((plugin_name, path))

    def list_for_plugin(self, plugin_name: str) -> List[PageDefinition]:
        """Returns all pages registered by one plugin."""
        return [page for (name, _), page in self._pages.items() if name == plugin_name]

    def list_all(self) -> List[PageDefinition]:
        """Returns all registered pages."""
        return list(self._pages.values())

    def clear(self) -> None:
        """Removes all registered pages."""
        self._pages.clear()

    def __len__(self) -> int:
        return len(self._pages)


PluginPages = PageRegistry
