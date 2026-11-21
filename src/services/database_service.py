#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Database service managing SQLite persistence for tasks and configurations."""

import functools
import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.config.config import config


def _synchronized(method):
    """Serializes access to the shared sqlite connection/cursor.

    The connection is opened with ``check_same_thread=False`` so it may be
    used from worker threads, but it still needs a lock because one cursor is
    shared by every caller.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"


def print_color(text: str, color: str):
    """Utility to print colored text in console."""
    print(f"{color}{text}{Colors.RESET}")


class DatabaseService:
    """Service wrapping SQLite database interactions."""

    def __init__(self, db_file: Optional[str] = None):
        self._lock = threading.RLock()
        if db_file:
            self.db_file = db_file
        else:
            # Check if root tasks.db exists and data/tasks.db doesn't
            if os.path.exists("tasks.db") and not os.path.exists(config.db_path):
                os.makedirs(os.path.dirname(config.db_path), exist_ok=True)
                shutil.copy2("tasks.db", config.db_path)
            self.db_file = config.db_path

        self._init_db()

    def _init_db(self):
        """Initializes tables and ensures schema migrations are applied."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_file)), exist_ok=True)
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        cursor = conn.cursor()

        # Check existing table schema
        cursor.execute("PRAGMA table_info(tasks)")
        cols = [col[1] for col in cursor.fetchall()]

        if cols and "interval_sec" not in cols:
            conn.close()
            os.remove(self.db_file)
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            cursor = conn.cursor()
            cols = []

        if cols:
            columns_to_add = [
                ("is_once", "INTEGER DEFAULT 0"),
                ("once_time", "TEXT"),
                ("message_index", "INTEGER DEFAULT 0"),
                ("messages_list", "TEXT"),
                ("chats_list", "TEXT"),
                ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
            ]
            for col_name, col_type in columns_to_add:
                if col_name not in cols:
                    try:
                        cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass
            conn.commit()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                message TEXT,
                media_path TEXT,
                media_type TEXT,
                interval_sec TEXT,
                is_active INTEGER DEFAULT 1,
                total_sent INTEGER DEFAULT 0,
                last_sent TEXT,
                is_once INTEGER DEFAULT 0,
                once_time TEXT,
                message_index INTEGER DEFAULT 0,
                messages_list TEXT,
                chats_list TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS plugin_config (
                id TEXT PRIMARY KEY,
                config TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS web_plugin_nav (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_name TEXT NOT NULL,
                page_path TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(plugin_name, page_path)
            )
            """
        )
        conn.commit()

        self.conn = conn
        self.cursor = cursor

    @_synchronized
    def add(
        self,
        chat_id: str,
        message: str,
        media_path: Optional[str],
        media_type: Optional[str],
        interval_config: Dict[str, Any],
        is_once: int = 0,
        once_time: Optional[str] = None,
        messages_list: Optional[List[str]] = None,
        chats_list: Optional[List[str]] = None,
    ) -> int:
        """Adds a new task to the database."""
        interval_json = json.dumps(interval_config)
        messages_json = json.dumps(messages_list) if messages_list else None
        chats_json = json.dumps(chats_list) if chats_list else None

        self.cursor.execute(
            """
            INSERT INTO tasks (chat_id, message, media_path, media_type, interval_sec, is_once, once_time, messages_list, chats_list, message_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                message,
                media_path,
                media_type,
                interval_json,
                is_once,
                str(once_time) if once_time else None,
                messages_json,
                chats_json,
                0,
            ),
        )
        self.conn.commit()
        return self.cursor.lastrowid

    @_synchronized
    def get_all(self) -> List[Tuple]:
        """Retrieves all tasks formatted with parsed json objects."""
        self.cursor.execute(
            """
            SELECT id, chat_id, message, media_path, media_type, interval_sec,
                   is_active, total_sent, last_sent, is_once, once_time,
                   message_index, messages_list, chats_list
            FROM tasks
            """
        )
        tasks = self.cursor.fetchall()
        result = []
        for t in tasks:
            row = list(t)
            # Parse interval
            if isinstance(row[5], int):
                row[5] = {"type": "interval", "seconds": row[5]}
            else:
                try:
                    row[5] = json.loads(row[5])
                except Exception:
                    row[5] = {"type": "interval", "seconds": 60}

            # Parse messages_list
            if row[12]:
                try:
                    row[12] = json.loads(row[12])
                except Exception:
                    row[12] = []
            else:
                row[12] = []

            # Parse chats_list
            if row[13]:
                try:
                    if isinstance(row[13], str):
                        parsed = json.loads(row[13])
                        # Handle double-encoded JSON: '"[\"me\"]"' -> '[\"me\"]' -> ['me']
                        if isinstance(parsed, str):
                            parsed = json.loads(parsed)
                        row[13] = parsed if isinstance(parsed, list) else [str(parsed)]
                    else:
                        row[13] = [str(row[13])]
                except Exception:
                    row[13] = [str(row[13])]
            else:
                row[13] = []

            result.append(tuple(row))
        return result

    @_synchronized
    def get_one(self, task_id: int) -> Optional[Tuple]:
        """Retrieves a single task by ID."""
        self.cursor.execute(
            """
            SELECT id, chat_id, message, media_path, media_type, interval_sec,
                   is_active, is_once, once_time, message_index, messages_list, chats_list
            FROM tasks WHERE id = ?
            """,
            (task_id,),
        )
        task = self.cursor.fetchone()
        if not task:
            return None

        row = list(task)
        if isinstance(row[5], int):
            row[5] = {"type": "interval", "seconds": row[5]}
        else:
            try:
                row[5] = json.loads(row[5])
            except Exception:
                row[5] = {"type": "interval", "seconds": 60}

        if row[10]:
            try:
                parsed = json.loads(row[10])
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                row[10] = parsed if isinstance(parsed, list) else []
            except Exception:
                row[10] = []
        if row[11]:
            try:
                parsed = json.loads(row[11])
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                row[11] = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                row[11] = []

        return tuple(row)

    @_synchronized
    def get_full(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves complete task record by ID as a dictionary."""
        self.cursor.execute(
            """
            SELECT id, chat_id, message, media_path, media_type, interval_sec,
                   is_active, total_sent, last_sent, is_once, once_time,
                   message_index, messages_list, chats_list, created_at
            FROM tasks WHERE id = ?
            """,
            (task_id,),
        )
        task = self.cursor.fetchone()
        if not task:
            return None

        row = list(task)
        if isinstance(row[5], int):
            interval_cfg = {"type": "interval", "seconds": row[5]}
        else:
            try:
                interval_cfg = json.loads(row[5])
            except Exception:
                interval_cfg = {"type": "interval", "seconds": 60}

        msgs_list = []
        if row[12]:
            try:
                parsed = json.loads(row[12])
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                msgs_list = parsed if isinstance(parsed, list) else []
            except Exception:
                msgs_list = []

        chats_list = []
        if row[13]:
            try:
                parsed = json.loads(row[13])
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                chats_list = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                chats_list = [str(row[13])]

        return {
            "id": row[0],
            "chat_id": row[1],
            "message": row[2],
            "media_path": row[3],
            "media_type": row[4],
            "interval_config": interval_cfg,
            "is_active": bool(row[6]),
            "total_sent": row[7] or 0,
            "last_sent": row[8],
            "is_once": bool(row[9]),
            "once_time": row[10],
            "message_index": row[11],
            "messages_list": msgs_list,
            "chats_list": chats_list,
            "created_at": row[14],
        }

    @_synchronized
    def get_active(self) -> List[Tuple]:
        """Retrieves all active tasks."""
        self.cursor.execute(
            """
            SELECT id, chat_id, message, media_path, media_type, interval_sec,
                   is_once, once_time, message_index, messages_list, chats_list
            FROM tasks WHERE is_active = 1
            """
        )
        tasks = self.cursor.fetchall()
        result = []
        for t in tasks:
            row = list(t)
            if isinstance(row[5], int):
                row[5] = {"type": "interval", "seconds": row[5]}
            else:
                try:
                    row[5] = json.loads(row[5])
                except Exception:
                    row[5] = {"type": "interval", "seconds": 60}

            if len(row) > 9 and row[9]:
                try:
                    row[9] = json.loads(row[9])
                except Exception:
                    row[9] = []
            else:
                if len(row) <= 9:
                    row.append([])
                else:
                    row[9] = []

            if len(row) > 10 and row[10]:
                try:
                    row[10] = json.loads(row[10])
                except Exception:
                    row[10] = []
            else:
                if len(row) <= 10:
                    row.append([])
                else:
                    row[10] = []

            result.append(tuple(row))
        return result

    @_synchronized
    def update_sent(self, task_id: int):
        """Updates send counter and last sent timestamp."""
        now = datetime.now().strftime("%H:%M:%S")
        self.cursor.execute(
            "UPDATE tasks SET total_sent = total_sent + 1, last_sent = ? WHERE id = ?",
            (now, task_id),
        )
        self.conn.commit()

    @_synchronized
    def update_message_index(self, task_id: int, index: int):
        """Updates rotation message pointer."""
        self.cursor.execute("UPDATE tasks SET message_index = ? WHERE id = ?", (index, task_id))
        self.conn.commit()

    @_synchronized
    def update_task(
        self,
        task_id: int,
        chat_id: Optional[str] = None,
        message: Optional[str] = None,
        media_path: Optional[str] = None,
        media_type: Optional[str] = None,
        interval_config: Optional[Dict[str, Any]] = None,
        is_once: Optional[int] = None,
        once_time: Optional[Any] = None,
        messages_list: Optional[List[str]] = None,
        chats_list: Optional[List[str]] = None,
    ):
        """Updates task fields."""
        if chat_id is not None:
            self.cursor.execute("UPDATE tasks SET chat_id = ? WHERE id = ?", (chat_id, task_id))
        if message is not None:
            self.cursor.execute("UPDATE tasks SET message = ? WHERE id = ?", (message, task_id))
        if media_path is not None:
            self.cursor.execute("UPDATE tasks SET media_path = ? WHERE id = ?", (media_path, task_id))
        if media_type is not None:
            self.cursor.execute("UPDATE tasks SET media_type = ? WHERE id = ?", (media_type, task_id))
        if interval_config is not None:
            self.cursor.execute("UPDATE tasks SET interval_sec = ? WHERE id = ?", (json.dumps(interval_config), task_id))
        if is_once is not None:
            self.cursor.execute("UPDATE tasks SET is_once = ? WHERE id = ?", (is_once, task_id))
        if once_time is not None:
            self.cursor.execute("UPDATE tasks SET once_time = ? WHERE id = ?", (str(once_time) if once_time else None, task_id))
        if messages_list is not None:
            self.cursor.execute("UPDATE tasks SET messages_list = ? WHERE id = ?", (json.dumps(messages_list) if messages_list else None, task_id))
        if chats_list is not None:
            self.cursor.execute("UPDATE tasks SET chats_list = ? WHERE id = ?", (json.dumps(chats_list) if chats_list else None, task_id))
        self.conn.commit()

    @_synchronized
    def set_active(self, task_id: int, active: bool):
        """Sets task active status."""
        self.cursor.execute("UPDATE tasks SET is_active = ? WHERE id = ?", (1 if active else 0, task_id))
        self.conn.commit()

    @_synchronized
    def delete(self, task_id: int):
        """Deletes task from database."""
        self.cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    @_synchronized
    def get_pinned_plugin_pages(self) -> List[Tuple[str, str]]:
        """Returns pinned plugin pages in menu order."""
        self.cursor.execute(
            "SELECT plugin_name, page_path FROM web_plugin_nav ORDER BY id"
        )
        return [(row[0], row[1]) for row in self.cursor.fetchall()]

    @_synchronized
    def set_plugin_page_pinned(self, plugin_name: str, page_path: str, pinned: bool) -> bool:
        """Pins or unpins a plugin page in the bottom navigation."""
        if pinned:
            self.cursor.execute(
                """
                INSERT OR IGNORE INTO web_plugin_nav (plugin_name, page_path)
                VALUES (?, ?)
                """,
                (plugin_name, page_path),
            )
        else:
            self.cursor.execute(
                "DELETE FROM web_plugin_nav WHERE plugin_name = ? AND page_path = ?",
                (plugin_name, page_path),
            )
        self.conn.commit()
        return bool(pinned)

    @_synchronized
    def is_plugin_page_pinned(self, plugin_name: str, page_path: str) -> bool:
        """Checks whether a plugin page is pinned in the bottom navigation."""
        self.cursor.execute(
            "SELECT 1 FROM web_plugin_nav WHERE plugin_name = ? AND page_path = ? LIMIT 1",
            (plugin_name, page_path),
        )
        return self.cursor.fetchone() is not None

    @_synchronized
    def get_plugin_config(self, plugin_id: str) -> Optional[str]:
        """Returns the stored raw JSON config for a plugin, if any."""
        self.cursor.execute(
            "SELECT config FROM plugin_config WHERE id = ?", (plugin_id,)
        )
        row = self.cursor.fetchone()
        return row[0] if row else None

    @_synchronized
    def save_plugin_config(self, plugin_id: str, config_json: str):
        """Stores the raw JSON config for a plugin."""
        self.cursor.execute(
            "INSERT OR REPLACE INTO plugin_config (id, config) VALUES (?, ?)",
            (plugin_id, config_json),
        )
        self.conn.commit()

    @_synchronized
    def close(self):
        """Closes sqlite connection."""
        try:
            self.conn.close()
        except Exception:
            pass
