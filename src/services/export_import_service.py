#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Service for exporting and importing task configurations to/from JSON."""

import json
import os
from pathlib import Path
from typing import Any, List

from src.services.logger import log
from src.services.path_security import is_private_media_path


class ExportImportService:
    """Manages serializing task definitions to JSON and bulk importing."""

    @staticmethod
    def export_tasks_data(db) -> List[dict]:
        """Builds a serializable list of all task entries."""
        tasks = db.get_all()
        export_data = []
        for t in tasks:
            export_data.append({
                "id": t[0],
                "chat_id": t[1],
                "message": t[2],
                "media_path": t[3],
                "media_type": t[4],
                "interval": t[5],
                "is_active": t[6],
                "total_sent": t[7],
                "last_sent": t[8],
                "is_once": t[9] if len(t) > 9 else 0,
                "once_time": t[10] if len(t) > 10 else None,
                "messages_list": t[12] if len(t) > 12 else None,
                "chats_list": t[13] if len(t) > 13 else None,
            })
        return export_data

    @staticmethod
    def export_tasks(db, filename: str = "tasks_backup.json") -> int:
        """Exports all task entries to JSON."""
        export_data = ExportImportService.export_tasks_data(db)
        if not export_data:
            return 0

        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
            return len(export_data)
        except Exception:
            return 0

    @staticmethod
    def import_tasks_data(db, import_data: List[dict]) -> int:
        """Imports tasks from an already parsed JSON list."""
        if not isinstance(import_data, list):
            return 0

        count = 0
        for task in import_data:
            if not isinstance(task, dict):
                continue

            media_path = task.get("media_path")
            if isinstance(media_path, str) and media_path.strip():
                try:
                    resolved = Path(media_path).expanduser().resolve(strict=False)
                    if is_private_media_path(resolved):
                        log.warning("Blocked private media_path during task import")
                        media_path = None
                    else:
                        media_path = str(resolved)
                except Exception:
                    media_path = None
            elif media_path not in (None, ""):
                media_path = None

            chat_id = task.get("chat_id")
            if not isinstance(chat_id, (str, int)):
                chat_id = ""
            message = task.get("message", "")
            if not isinstance(message, str):
                message = str(message) if message is not None else ""
            media_type = task.get("media_type")
            if media_type is not None and not isinstance(media_type, str):
                media_type = None
            interval_config = task.get("interval", {"type": "interval", "seconds": 60})
            if not isinstance(interval_config, dict):
                interval_config = {"type": "interval", "seconds": 60}
            is_once = 1 if task.get("is_once", 0) in (1, True, "1", "true") else 0
            once_time = task.get("once_time")
            if once_time is not None and not isinstance(once_time, str):
                once_time = str(once_time)
            messages_list = task.get("messages_list")
            if messages_list is not None and not isinstance(messages_list, list):
                messages_list = None
            chats_list = task.get("chats_list")
            if chats_list is not None and not isinstance(chats_list, (list, str)):
                chats_list = None

            db.add(
                chat_id=str(chat_id),
                message=message,
                media_path=media_path,
                media_type=media_type,
                interval_config=interval_config,
                is_once=is_once,
                once_time=once_time,
                messages_list=messages_list,
                chats_list=chats_list,
            )
            count += 1
        return count

    @staticmethod
    def import_tasks(db, filename: str = "tasks_backup.json") -> int:
        """Imports tasks from a JSON backup file."""
        if not os.path.exists(filename):
            return 0

        try:
            with open(filename, "r", encoding="utf-8") as f:
                import_data = json.load(f)
            return ExportImportService.import_tasks_data(db, import_data)
        except Exception:
            return 0
