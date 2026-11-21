#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
from aiohttp import web

from src.config.config import config
from src.web import sessions

_LOGIN_ATTEMPTS = {}
_BLOCKED_IPS = {}


SESSION_COOKIE = "telesender_session"


def get_web_session(session_id):
    return sessions.get_session(session_id)


def create_web_session(method="password", telegram_id=None):
    data = {"method": method}
    if telegram_id is not None:
        data["telegram_id"] = int(telegram_id)
    return sessions.create_session(data)


def delete_web_session(session_id):
    sessions.delete_session(session_id)


def set_session_cookie(response, session_id):
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        max_age=30 * 24 * 3600,
        samesite='Lax',
        path='/',
    )


def clear_session_cookie(response):
    response.del_cookie(SESSION_COOKIE, path='/')


def get_client_ip(request):
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    if request.remote:
        return request.remote
    return 'unknown'


def is_blocked(request):
    ip = get_client_ip(request)
    blocked_until = _BLOCKED_IPS.get(ip)
    if blocked_until and time.time() < blocked_until:
        return True, blocked_until
    if blocked_until and time.time() >= blocked_until:
        _BLOCKED_IPS.pop(ip, None)
    return False, None


def record_failed_attempt(request):
    ip = get_client_ip(request)
    attempts = _LOGIN_ATTEMPTS.get(ip, [])
    now = time.time()
    attempts = [t for t in attempts if now - t < 3600]
    attempts.append(now)
    _LOGIN_ATTEMPTS[ip] = attempts
    if len(attempts) >= config.web_auth_max_attempts:
        _BLOCKED_IPS[ip] = now + config.web_auth_lockout_minutes * 60
        return True
    return False


def clear_failed_attempts(request):
    ip = get_client_ip(request)
    _LOGIN_ATTEMPTS.pop(ip, None)
    _BLOCKED_IPS.pop(ip, None)
