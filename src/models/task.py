#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data models for tasks and execution state."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Task:
    """Represents a scheduled messaging task."""

    id: int
    chat_id: str
    message: str
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    interval_sec: Dict[str, Any] = field(default_factory=lambda: {"type": "interval", "seconds": 60})
    is_active: bool = True
    total_sent: int = 0
    last_sent: Optional[str] = None
    is_once: bool = False
    once_time: Optional[str] = None
    message_index: int = 0
    messages_list: List[str] = field(default_factory=list)
    chats_list: List[str] = field(default_factory=list)
    created_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Task":
        """Creates a Task instance from a database row."""
        # Handles various row lengths from legacy schema queries
        tid = row[0]
        chat_id = str(row[1])
        message = row[2] or ""
        media_path = row[3] or None
        media_type = row[4] or None
        interval_cfg = row[5] if isinstance(row[5], dict) else {"type": "interval", "seconds": 60}
        
        is_active = bool(row[6]) if len(row) > 6 else True
        total_sent = row[7] if len(row) > 7 and isinstance(row[7], int) else 0
        last_sent = row[8] if len(row) > 8 else None
        is_once = bool(row[9]) if len(row) > 9 else False
        once_time = row[10] if len(row) > 10 else None
        msg_idx = row[11] if len(row) > 11 and isinstance(row[11], int) else 0
        msg_list = row[12] if len(row) > 12 and isinstance(row[12], list) else []
        chats = row[13] if len(row) > 13 and isinstance(row[13], list) else []

        return cls(
            id=tid,
            chat_id=chat_id,
            message=message,
            media_path=media_path,
            media_type=media_type,
            interval_sec=interval_cfg,
            is_active=is_active,
            total_sent=total_sent,
            last_sent=last_sent,
            is_once=is_once,
            once_time=once_time,
            message_index=msg_idx,
            messages_list=msg_list,
            chats_list=chats,
        )
