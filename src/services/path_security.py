#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for validating filesystem paths used by user-controlled features."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from src.config.config import config


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except Exception:
        return False


def _configured_sensitive_paths() -> list[Path]:
    candidates = [
        PROJECT_ROOT / "data",
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / ".git",
    ]
    for attr in ("db_path", "session_path", "log_file"):
        value = getattr(config, attr, None)
        if not value:
            continue
        p = Path(str(value))
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        candidates.append(p)
    # Session files may have suffixes like .session-journal
    session_path = getattr(config, "session_path", None)
    if session_path:
        sp = Path(str(session_path))
        if not sp.is_absolute():
            sp = PROJECT_ROOT / sp
        candidates.append(Path(str(sp) + ".session"))
        candidates.append(Path(str(sp) + "-journal"))
        candidates.append(Path(str(sp) + "-wal"))
        candidates.append(Path(str(sp) + "-shm"))
    # Common private dirs
    candidates.append(PROJECT_ROOT / "data" / "backups")
    return candidates


def is_private_media_path(path: str | os.PathLike[str]) -> bool:
    """Returns True if path points to private/service data that must not be sent."""
    if not path:
        return False
    try:
        resolved = _resolve(path)
    except Exception:
        return True

    sensitive = [_resolve(p) for p in _configured_sensitive_paths()]
    for base in sensitive:
        if resolved == base or _is_relative_to(resolved, base):
            return True

    # Never allow Telegram session sqlite files or env-like secrets by name.
    name = resolved.name.lower()
    if name == ".env" or name.endswith(".session") or name.endswith(".session-journal"):
        return True
    if resolved.suffix.lower() in {".env"}:
        return True

    # Protect data directory even if config points elsewhere.
    data_dir = _resolve(PROJECT_ROOT / "data")
    if resolved == data_dir or _is_relative_to(resolved, data_dir):
        return True

    return False


def validate_media_path(path: Optional[str]) -> Optional[str]:
    """Validates a user-supplied media path.

    Returns normalized absolute path string, or None for empty.
    Raises ValueError if path is unsafe.
    """
    if path is None:
        return None
    if not isinstance(path, str):
        raise ValueError("Invalid media path")
    raw = path.strip()
    if not raw:
        return None
    # Disallow null bytes and obvious control chars
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError("Invalid media path")

    resolved = _resolve(raw)
    if is_private_media_path(resolved):
        raise ValueError("Access to private data path is denied")
    return str(resolved)
