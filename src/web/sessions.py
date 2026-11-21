#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side web sessions and one-time bot login codes.

Sessions are persisted to ``data/web_sessions.json`` so that restarting the
bot does not log everybody out. The file only contains opaque random session
IDs, their expiry time and a small payload (login method / Telegram ID).

One-time bot login codes stay in memory: they live for two minutes and are
consumed immediately, so persisting them would only add attack surface.
"""

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple


logger = logging.getLogger(__name__)

_SESSION_TTL_SECONDS = 30 * 24 * 3600
_BOT_CODE_TTL_SECONDS = 120

_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "web_sessions.json"

_sessions: Dict[str, Tuple[float, dict]] = {}
_bot_login_codes: Dict[str, Tuple[float, int]] = {}
_lock = threading.Lock()


def _load_sessions() -> Tuple[Dict[str, Tuple[float, dict]], bool]:
    """Read persisted sessions, dropping malformed or expired entries.

    Returns the usable sessions and whether the on-disk store contained
    entries that were dropped (so it can be rewritten without them).
    """
    try:
        raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read web sessions from {_STORE_PATH}: {e}")
        return {}, False

    if not isinstance(raw, dict):
        logger.warning(f"Ignoring malformed web session store: {_STORE_PATH}")
        return {}, False

    now = time.time()
    sessions: Dict[str, Tuple[float, dict]] = {}
    for session_id, item in raw.items():
        if not isinstance(session_id, str) or not isinstance(item, list) or len(item) != 2:
            continue
        try:
            expiry = float(item[0])
        except (TypeError, ValueError):
            continue
        data = item[1]
        if expiry <= now or not isinstance(data, dict):
            continue
        sessions[session_id] = (expiry, data)
    return sessions, len(sessions) != len(raw)


def _save_sessions_locked() -> None:
    """Atomically persist the session table (call with ``_lock`` held)."""
    payload = {
        session_id: [expiry, data]
        for session_id, (expiry, data) in _sessions.items()
    }
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _STORE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, _STORE_PATH)
    except OSError as e:
        logger.warning(f"Could not persist web sessions to {_STORE_PATH}: {e}")


_sessions, _store_needs_prune = _load_sessions()

if _store_needs_prune:
    # Rewrite the file once so expired/invalid records do not linger on disk
    # forever when the bot was off while they expired.
    with _lock:
        _save_sessions_locked()


def _cleanup_locked(now: Optional[float] = None) -> bool:
    """Drop expired entries; return whether anything was removed."""
    now = now or time.time()
    expired_sessions = [sid for sid, (exp, _) in _sessions.items() if exp <= now]
    for sid in expired_sessions:
        _sessions.pop(sid, None)
    expired_codes = [code for code, (exp, _) in _bot_login_codes.items() if exp <= now]
    for code in expired_codes:
        _bot_login_codes.pop(code, None)
    return bool(expired_sessions)


def create_session(data: Optional[dict] = None, ttl: int = _SESSION_TTL_SECONDS) -> str:
    now = time.time()
    session_id = secrets.token_urlsafe(32)
    payload = dict(data or {})
    payload.setdefault("created_at", now)
    with _lock:
        _cleanup_locked(now)
        _sessions[session_id] = (now + max(60, int(ttl)), payload)
        _save_sessions_locked()
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    if not session_id:
        return None
    now = time.time()
    with _lock:
        removed = _cleanup_locked(now)
        if removed:
            _save_sessions_locked()
        item = _sessions.get(session_id)
        if not item:
            return None
        exp, data = item
        if exp <= now:
            _sessions.pop(session_id, None)
            _save_sessions_locked()
            return None
        return dict(data)


def delete_session(session_id: str) -> None:
    if not session_id:
        return
    with _lock:
        if _sessions.pop(session_id, None) is not None:
            _save_sessions_locked()


def create_bot_login_code(telegram_id: int, ttl: int = _BOT_CODE_TTL_SECONDS) -> str:
    now = time.time()
    code = secrets.token_urlsafe(32)
    with _lock:
        _cleanup_locked(now)
        _bot_login_codes[code] = (now + max(30, int(ttl)), int(telegram_id))
    return code


def consume_bot_login_code(code: str) -> Optional[int]:
    if not code:
        return None
    now = time.time()
    with _lock:
        _cleanup_locked(now)
        item = _bot_login_codes.pop(code, None)
        if not item:
            return None
        exp, telegram_id = item
        if exp <= now:
            return None
        return telegram_id
