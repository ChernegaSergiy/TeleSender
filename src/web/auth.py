#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web authentication helpers: sessions, cookies, client IP and rate limiting."""

import ipaddress
import threading
import time
from collections import deque

from src.config.config import config
from src.web import sessions

_LOGIN_ATTEMPTS = {}
_BLOCKED_IPS = {}
_GLOBAL_LOGIN_ATTEMPTS = deque()
_AUTH_STATE_LOCK = threading.Lock()

_MAX_TRACKED_IPS = 2048
_MAX_GLOBAL_ATTEMPTS = 4096
_GLOBAL_LOGIN_WINDOW_SECONDS = 60
_GLOBAL_LOGIN_MAX_ATTEMPTS = 120

SESSION_COOKIE = "telesender_session"


def _now() -> float:
    return time.time()


def _cleanup_auth_state_locked(now: float | None = None) -> None:
    now = now or _now()
    expired_ips = [ip for ip, until in _BLOCKED_IPS.items() if until <= now]
    for ip in expired_ips:
        _BLOCKED_IPS.pop(ip, None)

    stale_ips = []
    for ip, attempts in _LOGIN_ATTEMPTS.items():
        fresh = [t for t in attempts if now - t < 3600]
        if fresh:
            _LOGIN_ATTEMPTS[ip] = fresh
        else:
            stale_ips.append(ip)
    for ip in stale_ips:
        _LOGIN_ATTEMPTS.pop(ip, None)

    while _GLOBAL_LOGIN_ATTEMPTS and now - _GLOBAL_LOGIN_ATTEMPTS[0] > _GLOBAL_LOGIN_WINDOW_SECONDS:
        _GLOBAL_LOGIN_ATTEMPTS.popleft()

    # Bound memory usage under abusive traffic.
    if len(_LOGIN_ATTEMPTS) > _MAX_TRACKED_IPS:
        for ip in sorted(_LOGIN_ATTEMPTS.keys())[: len(_LOGIN_ATTEMPTS) - _MAX_TRACKED_IPS]:
            _LOGIN_ATTEMPTS.pop(ip, None)
    if len(_BLOCKED_IPS) > _MAX_TRACKED_IPS:
        for ip in sorted(_BLOCKED_IPS.keys(), key=lambda k: _BLOCKED_IPS[k])[: len(_BLOCKED_IPS) - _MAX_TRACKED_IPS]:
            _BLOCKED_IPS.pop(ip, None)
    while len(_GLOBAL_LOGIN_ATTEMPTS) > _MAX_GLOBAL_ATTEMPTS:
        _GLOBAL_LOGIN_ATTEMPTS.popleft()


def get_web_session(session_id):
    return sessions.get_session(session_id)


def create_web_session(method="password", telegram_id=None):
    data = {"method": method}
    if telegram_id is not None:
        data["telegram_id"] = int(telegram_id)
    return sessions.create_session(data)


def delete_web_session(session_id):
    sessions.delete_session(session_id)


def _trusted_proxy_networks():
    networks = []
    for raw in getattr(config, "web_trusted_proxies", []) or []:
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted_proxy(ip: str) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_proxy_networks())


def _request_remote_ip(request) -> str:
    remote = request.remote
    if remote:
        return str(remote).strip()
    transport = request.transport
    peer = transport.get_extra_info("peername") if transport else None
    if isinstance(peer, (tuple, list)) and peer:
        return str(peer[0]).strip()
    return "unknown"


def _is_https_request(request) -> bool:
    if request.secure:
        return True
    remote = _request_remote_ip(request)
    if not _is_trusted_proxy(remote):
        return False
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto:
        proto = forwarded_proto.split(",")[0].strip().lower()
        return proto == "https"
    return False


def is_trusted_origin(request) -> bool:
    """Validates Origin/Referer for browser-initiated state-changing requests."""
    origin = request.headers.get("Origin")
    if not origin:
        referer = request.headers.get("Referer")
        origin = referer
    if not origin:
        # Non-browser clients may omit Origin; keep compatibility for explicit API calls.
        return True
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(origin)
        if not parts.scheme or not parts.netloc:
            return False
        origin_netloc = parts.netloc.lower()
        origin_scheme = parts.scheme.lower()

        host = request.headers.get("Host", "").lower()
        if host and origin_netloc == host:
            # Only accept same scheme as seen by the app/proxy.
            req_scheme = "https" if _is_https_request(request) else "http"
            return origin_scheme == req_scheme

        # Allow configured public URL origin (tunnel/domain deployments).
        public = (getattr(config, "web_public_url", "") or "").strip()
        if public:
            pub = urlsplit(public)
            if pub.netloc.lower() == origin_netloc and pub.scheme.lower() == origin_scheme:
                return True
    except Exception:
        return False
    return False


def cookie_secure(request=None) -> bool:
    if request is None:
        return False
    return _is_https_request(request)


def get_client_ip(request):
    remote = _request_remote_ip(request)

    # Trust forwarded headers only from explicitly configured trusted proxies.
    if _is_trusted_proxy(remote):
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
    return remote


def set_session_cookie(response, session_id, request=None):
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        max_age=30 * 24 * 3600,
        samesite="Lax",
        secure=cookie_secure(request),
        path="/",
    )


def clear_session_cookie(response, request=None):
    response.del_cookie(
        SESSION_COOKIE,
        path="/",
        secure=cookie_secure(request),
        samesite="Lax",
    )


TWO_FACTOR_COOKIE = "telesender_2fa_req"


def set_2fa_cookie(response, req_id: str, request=None, max_age: int = 120):
    response.set_cookie(
        TWO_FACTOR_COOKIE,
        req_id,
        httponly=True,
        max_age=max_age,
        samesite="Lax",
        secure=cookie_secure(request),
        path="/",
    )


def clear_2fa_cookie(response, request=None):
    response.del_cookie(
        TWO_FACTOR_COOKIE,
        path="/",
        secure=cookie_secure(request),
        samesite="Lax",
    )


def is_blocked(request):
    ip = get_client_ip(request)
    now = _now()
    with _AUTH_STATE_LOCK:
        _cleanup_auth_state_locked(now)
        blocked_until = _BLOCKED_IPS.get(ip)
        if blocked_until and now < blocked_until:
            return True, blocked_until
        if blocked_until and now >= blocked_until:
            _BLOCKED_IPS.pop(ip, None)
        # Global rate limit protects against distributed/IP-spoofed attempts.
        recent_global = len(_GLOBAL_LOGIN_ATTEMPTS)
        if recent_global >= _GLOBAL_LOGIN_MAX_ATTEMPTS:
            return True, now + 60
    return False, None


def record_failed_attempt(request):
    ip = get_client_ip(request)
    now = _now()
    with _AUTH_STATE_LOCK:
        _cleanup_auth_state_locked(now)
        attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < 3600]
        attempts.append(now)
        _LOGIN_ATTEMPTS[ip] = attempts
        _GLOBAL_LOGIN_ATTEMPTS.append(now)

        if len(attempts) >= config.web_auth_max_attempts:
            _BLOCKED_IPS[ip] = now + config.web_auth_lockout_minutes * 60
            return True
        if len(_GLOBAL_LOGIN_ATTEMPTS) >= _GLOBAL_LOGIN_MAX_ATTEMPTS:
            return True
    return False


def clear_failed_attempts(request):
    ip = get_client_ip(request)
    with _AUTH_STATE_LOCK:
        _LOGIN_ATTEMPTS.pop(ip, None)
        _BLOCKED_IPS.pop(ip, None)
        _cleanup_auth_state_locked(_now())
