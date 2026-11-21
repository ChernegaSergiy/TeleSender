#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-memory cache for rendered HTML pages."""

import hashlib
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

from aiohttp import web

from src.services.translator import lang

CACHE_HEADER = "X-Page-Cache"
TTL_HEADER = "X-Page-Cache-TTL"

# Never cache a single page larger than this.
MAX_PAGE_BYTES = 512 * 1024

# Headers that must not be replayed from a cached response.
_SKIP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "keep-alive",
    "set-cookie",
    "transfer-encoding",
    "vary",
    TTL_HEADER.lower(),
}

# Paths that never contain rendered HTML pages.
_NON_HTML_PREFIXES = ("/api/", "/static/", "/assets/", "/ws/")


class PageCache:
    """Small LRU cache holding serialized HTML responses with a TTL."""

    def __init__(
        self,
        ttl: float = 15.0,
        max_entries: int = 128,
        max_bytes: int = 16 * 1024 * 1024,
    ):
        self.ttl = max(0.0, float(ttl or 0))
        self.max_entries = max(1, int(max_entries or 128))
        self.max_bytes = max(0, int(max_bytes or 0))
        self._entries: "OrderedDict[Tuple[str, str, str, str], Tuple]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def clear(self) -> None:
        """Drops every cached page."""
        self._entries.clear()
        self._bytes = 0

    def _drop(self, key) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry[-1]

    def get(self, key: Tuple[str, str, str, str]):
        if not self.enabled:
            return None

        entry = self._entries.get(key)
        if entry is None:
            return None

        expires_at, status, body, content_type, charset, headers, _size = entry
        if time.monotonic() >= expires_at:
            self._drop(key)
            return None

        self._entries.move_to_end(key)
        self.hits += 1
        return status, body, content_type, charset, headers

    def set(
        self,
        key: Tuple[str, str, str, str],
        ttl: float,
        status: int,
        body: bytes,
        content_type: str,
        charset: Optional[str],
        headers: Dict[str, str],
    ) -> None:
        if not self.enabled or ttl <= 0 or len(body) > MAX_PAGE_BYTES:
            return

        size = len(body) + 256
        size += sum(len(name) + len(value) for name, value in headers.items())
        size += sum(len(part) for part in key)
        if self.max_bytes and size > self.max_bytes:
            return

        self._drop(key)
        self._entries[key] = (
            time.monotonic() + ttl,
            status,
            body,
            content_type,
            charset,
            dict(headers),
            size,
        )
        self._bytes += size

        while self._entries and (
            len(self._entries) > self.max_entries
            or (self.max_bytes and self._bytes > self.max_bytes)
        ):
            old_key, old_entry = self._entries.popitem(last=False)
            self._bytes -= old_entry[-1]


PAGE_CACHE_KEY = web.AppKey("page_cache", PageCache)


def _cache_key(request: web.Request) -> Tuple[str, str, str, str]:
    # Pages can render different content for the logged-in session (navigation,
    # auth widgets), so cookies must be part of the key. Hashing keeps the raw
    # cookie value (which is the session token) out of the cache structure.
    cookie = request.headers.get("Cookie", "")
    cookie_key = hashlib.sha256(cookie.encode("utf-8")).hexdigest()[:16] if cookie else ""
    return (request.rel_url.path, request.rel_url.query_string, lang.get_lang(), cookie_key)


def _request_cacheable(request: web.Request) -> bool:
    if request.path.startswith(_NON_HTML_PREFIXES):
        return False
    if request.headers.get("Authorization"):
        return False
    if request.headers.get("Range") or request.headers.get("Upgrade"):
        return False
    return True


def _response_cacheable(response: web.StreamResponse) -> bool:
    if not isinstance(response, web.Response) or response.status != 200:
        return False
    if response.content_type != "text/html" or response.body is None:
        return False
    if response.headers.get("Set-Cookie") or response.headers.get("Content-Encoding"):
        return False
    # A Vary header means the body depends on request headers we do not key on.
    if response.headers.get("Vary"):
        return False

    cache_control = response.headers.get("Cache-Control", "").lower()
    if "no-store" in cache_control or "private" in cache_control:
        return False
    return True


def _page_ttl(response: web.Response, default: float) -> float:
    raw = response.headers.get(TTL_HEADER)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _build_response(cached, cache_status: str) -> web.Response:
    status, body, content_type, charset, headers = cached
    response = web.Response(
        body=body,
        status=status,
        content_type=content_type,
        charset=charset,
        headers=headers,
    )
    response.headers[CACHE_HEADER] = cache_status
    return response


@web.middleware
async def cache_html_pages(request: web.Request, handler) -> web.StreamResponse:
    """Serves rendered HTML from memory when it is still fresh."""
    cache: Optional[PageCache] = request.app.get(PAGE_CACHE_KEY)
    if (
        cache is None
        or not cache.enabled
        or request.method != "GET"
        or not _request_cacheable(request)
    ):
        response = await handler(request)
        if isinstance(response, web.StreamResponse):
            response.headers.pop(TTL_HEADER, None)
        return response

    key = _cache_key(request)
    cached = cache.get(key)
    if cached is not None:
        return _build_response(cached, "HIT")

    response = await handler(request)
    cache.misses += 1

    if not _response_cacheable(response):
        response.headers.pop(TTL_HEADER, None)
        return response

    ttl = _page_ttl(response, cache.ttl)
    response.headers.pop(TTL_HEADER, None)

    if ttl <= 0:
        return response

    body = response.body
    if isinstance(body, str):
        body = body.encode(response.charset or "utf-8")

    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"

    headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in _SKIP_HEADERS
    }
    cache.set(
        key,
        ttl,
        response.status,
        body,
        response.content_type,
        response.charset,
        headers,
    )

    response.headers[CACHE_HEADER] = "MISS"
    return response
